from __future__ import annotations

from math import ceil
from typing import Callable
from typing import Any
import json

import httpx

from booking_cli.config import Settings
from booking_cli.graphql import (
    PAGINATION_PATHS,
    RESULT_PATHS,
    SEARCH_META_PATHS,
    build_autocomplete_headers,
    build_autocomplete_payload,
    build_browser_headers,
    build_graphql_headers,
    build_graphql_payload,
    build_search_url,
    extract_embedded_search_response,
    extract_csrf_token,
    is_waf_challenge,
)
from booking_cli.models import Coordinates, Destination, HotelResult, Pagination, SearchRequest, SearchResponse
from booking_cli.session import (
    NullSessionCache,
    SessionCache,
    SessionState,
    apply_session_to_http_client,
    capture_http_session,
)


class BookingClientError(RuntimeError):
    pass


class BookingBlockedError(BookingClientError):
    pass


class BookingAuthenticationError(BookingClientError):
    pass


class BookingClient:
    def __init__(
        self,
        settings: Settings,
        http_client: httpx.Client | None = None,
        session_cache: SessionCache | None = None,
        browser_bootstrapper: Callable[[str, Settings], SessionState] | None = None,
    ) -> None:
        self.settings = settings
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(http2=True, timeout=settings.timeout, follow_redirects=True)
        self._session_cache = session_cache or (SessionCache(settings.session_cache_path) if self._owns_client else NullSessionCache())
        self._browser_bootstrapper = browser_bootstrapper

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def resolve_destination(self, query: str, *, limit: int = 5) -> tuple[Destination, ...]:
        response = self._http.post(
            self.settings.autocomplete_url,
            headers=build_autocomplete_headers(
                user_agent=self.settings.user_agent,
                language=self.settings.language,
            ),
            content=build_autocomplete_payload(
                query,
                language=self.settings.language,
                aid=self.settings.aid,
                size=limit,
            ),
        )
        self._ensure_ok(response, "destination resolution")

        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise BookingClientError("Booking autocomplete returned invalid JSON.") from exc

        results = payload.get("results")
        if not isinstance(results, list):
            raise BookingClientError("Booking autocomplete returned an unexpected payload.")

        destinations = tuple(
            Destination.from_autocomplete(query, item)
            for item in results
            if isinstance(item, dict)
        )
        if not destinations:
            raise BookingClientError(f'No destination matches found for "{query}".')
        return destinations

    def search(self, request: SearchRequest) -> SearchResponse:
        destination = self._resolve_destination_for_request(request)
        referer = build_search_url(
            base_url=self.settings.base_url,
            language=request.language,
            aid=self.settings.aid,
            request=request,
            destination=destination,
        )

        payload = build_graphql_payload(request, destination)
        response_payload, session_meta = self._run_search_request(
            request=request,
            referer=referer,
            payload=payload,
        )

        raw_results = self._extract_first_path(response_payload, RESULT_PATHS)
        if not isinstance(raw_results, list):
            errors = response_payload.get("errors")
            if isinstance(errors, list) and errors:
                message = errors[0].get("message") or "Booking GraphQL returned an error."
                raise BookingClientError(str(message))
            raise BookingClientError(
                "Booking GraphQL returned a response shape this CLI does not understand yet."
            )

        normalized = tuple(self._normalize_result(item) for item in raw_results if isinstance(item, dict))
        filtered = self._apply_local_filters(normalized, request)
        visible_results = filtered[: request.limit]
        pagination_data = self._extract_first_path(response_payload, PAGINATION_PATHS) or {}
        search_meta = self._extract_first_path(response_payload, SEARCH_META_PATHS) or {}
        pagination = self._build_pagination(request, visible_results, pagination_data)

        return SearchResponse(
            request=request,
            destination=destination,
            pagination=pagination,
            results=visible_results,
            endpoint=self.settings.graphql_url,
            meta={
                "sort_applied": request.sort,
                "filters": {
                    "min_review_score": request.min_review_score,
                    "stars": list(request.stars),
                },
                "search_meta": search_meta if isinstance(search_meta, dict) else {},
                "session": session_meta,
                "verification": {
                    "autocomplete": "verified-live-2026-03-17",
                    "graphql_endpoint": "verified-live-2026-03-17",
                    "search_page": "waf-challenge-observed-2026-03-17",
                },
            },
            raw_response=response_payload,
        )

    def _resolve_destination_for_request(self, request: SearchRequest) -> Destination:
        if request.dest_id and request.dest_type:
            return Destination.from_override(
                query=request.destination or request.dest_id,
                dest_id=request.dest_id,
                dest_type=request.dest_type,
            )

        if not request.destination:
            raise BookingClientError("A destination string is required unless --dest-id and --dest-type are set.")

        return self.resolve_destination(request.destination, limit=1)[0]

    def _run_search_request(
        self,
        *,
        request: SearchRequest,
        referer: str,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        cached_session = self._load_cached_session()
        if cached_session is not None:
            apply_session_to_http_client(self._http, cached_session)
            try:
                embedded = self._embedded_search_response(referer)
                if embedded is not None:
                    return embedded, {
                        "cache_hit": True,
                        "source": cached_session.source,
                        "transport": "embedded-html",
                        "updated_at": cached_session.updated_at.isoformat(),
                    }
                response = self._graphql_request(
                    request=request,
                    referer=referer,
                    csrf_token=cached_session.csrf_token,
                    payload=payload,
                    retry_on_auth=False,
                )
                return response, {
                    "cache_hit": True,
                    "source": cached_session.source,
                    "transport": "graphql",
                    "updated_at": cached_session.updated_at.isoformat(),
                }
            except BookingAuthenticationError:
                self._session_cache.clear()
                self._clear_http_cookies()

        try:
            embedded = self._embedded_search_response(referer)
            if embedded is not None:
                return embedded, {
                    "cache_hit": False,
                    "source": "http",
                    "transport": "embedded-html",
                    "updated_at": None,
                }
        except BookingAuthenticationError:
            pass

        session = self._refresh_session(referer)
        embedded = self._embedded_search_response(referer)
        if embedded is not None:
            return embedded, {
                "cache_hit": False,
                "source": session.source,
                "transport": "embedded-html",
                "updated_at": session.updated_at.isoformat(),
            }
        response = self._graphql_request(
            request=request,
            referer=referer,
            csrf_token=session.csrf_token,
            payload=payload,
            retry_on_auth=True,
        )
        return response, {
            "cache_hit": False,
            "source": session.source,
            "transport": "graphql",
            "updated_at": session.updated_at.isoformat(),
        }

    def _embedded_search_response(self, search_url: str) -> dict[str, Any] | None:
        response = self._http.get(
            search_url,
            headers=build_browser_headers(
                user_agent=self.settings.user_agent,
                language=self.settings.language,
            ),
        )
        self._ensure_ok(response, "search page fetch")

        embedded = extract_embedded_search_response(response.text)
        if embedded is not None:
            return embedded
        if is_waf_challenge(response.text):
            raise BookingAuthenticationError("Booking returned its anti-bot challenge page for the current session.")
        return None

    def _fetch_search_session(self, search_url: str) -> SessionState:
        response = self._http.get(
            search_url,
            headers=build_browser_headers(
                user_agent=self.settings.user_agent,
                language=self.settings.language,
            ),
        )
        self._ensure_ok(response, "search page fetch")
        if is_waf_challenge(response.text):
            raise BookingBlockedError(
                "Booking returned its anti-bot challenge page before a CSRF token could be extracted. "
                "This CLI does not bypass Booking protections."
            )

        csrf_token = extract_csrf_token(response.text)
        if not csrf_token:
            raise BookingClientError(
                "Booking search page did not contain a CSRF token. The internal page format may have changed."
            )
        return capture_http_session(
            client=self._http,
            csrf_token=csrf_token,
            base_url=self.settings.base_url,
            language=self.settings.language,
            user_agent=self.settings.user_agent,
            source="http",
        )

    def _graphql_request(
        self,
        *,
        request: SearchRequest,
        referer: str,
        csrf_token: str,
        payload: dict[str, Any],
        retry_on_auth: bool,
    ) -> dict[str, Any]:
        response = self._http.post(
            f"{self.settings.graphql_url}?lang={request.language}",
            headers=build_graphql_headers(
                user_agent=self.settings.user_agent,
                language=request.language,
                csrf_token=csrf_token,
                referer=referer,
            ),
            json=payload,
        )
        if response.status_code in (401, 403) and retry_on_auth:
            refreshed = self._refresh_session(referer)
            return self._graphql_request(
                request=request,
                referer=referer,
                csrf_token=refreshed.csrf_token,
                payload=payload,
                retry_on_auth=False,
            )
        if response.status_code in (401, 403):
            raise BookingAuthenticationError("Booking GraphQL rejected the current session.")

        self._ensure_ok(response, "GraphQL search")
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise BookingClientError("Booking GraphQL returned invalid JSON.") from exc

    def _refresh_session(self, search_url: str) -> SessionState:
        try:
            session = self._fetch_search_session(search_url)
        except BookingBlockedError as exc:
            if not self.settings.browser_bootstrap:
                raise exc
            try:
                session = self._bootstrap_browser_session(search_url)
            except BookingBlockedError as browser_exc:
                raise browser_exc from exc

        apply_session_to_http_client(self._http, session)
        self._session_cache.save(session)
        return session

    def _bootstrap_browser_session(self, search_url: str) -> SessionState:
        try:
            if self._browser_bootstrapper is not None:
                return self._browser_bootstrapper(search_url, self.settings)

            from booking_cli.browser import bootstrap_browser_session

            return bootstrap_browser_session(search_url, self.settings)
        except ImportError as exc:
            raise BookingBlockedError("Browser bootstrap support could not be loaded.") from exc
        except RuntimeError as exc:
            message = str(exc).strip() or "Browser bootstrap failed."
            raise BookingBlockedError(
                "Booking blocked the plain HTTP fetch, and automatic browser bootstrap did not succeed. "
                f"{message}"
            ) from exc

    def _load_cached_session(self) -> SessionState | None:
        cached = self._session_cache.load()
        if cached is None:
            return None
        cached = cached.without_expired_cookies()
        if not cached.applies_to(
            base_url=self.settings.base_url,
            language=self.settings.language,
            user_agent=self.settings.user_agent,
        ):
            return None
        if not cached.is_fresh(self.settings.session_ttl_minutes):
            self._session_cache.clear()
            return None
        return cached

    def _clear_http_cookies(self) -> None:
        cookies = getattr(self._http, "cookies", None)
        clear = getattr(cookies, "clear", None)
        if callable(clear):
            clear()

    def _apply_local_filters(
        self,
        results: tuple[HotelResult, ...],
        request: SearchRequest,
    ) -> tuple[HotelResult, ...]:
        filtered = list(results)

        if request.min_review_score is not None:
            filtered = [
                item
                for item in filtered
                if item.review_score is not None and item.review_score >= request.min_review_score
            ]

        if request.stars:
            allowed = set(request.stars)
            filtered = [item for item in filtered if item.stars in allowed]

        sort_name = request.sort
        if sort_name == "price":
            filtered.sort(key=lambda item: (item.price is None, item.price if item.price is not None else 0))
        elif sort_name == "review-score":
            filtered.sort(
                key=lambda item: (
                    item.review_score is None,
                    -(item.review_score or 0),
                    -(item.review_count or 0),
                )
            )
        elif sort_name == "distance":
            filtered.sort(
                key=lambda item: (
                    item.distance_to_center_meters is None,
                    item.distance_to_center_meters if item.distance_to_center_meters is not None else 0,
                )
            )
        elif sort_name == "stars":
            filtered.sort(key=lambda item: (item.stars is None, -(item.stars or 0), item.name.lower()))
        elif sort_name == "name":
            filtered.sort(key=lambda item: item.name.lower())

        return tuple(filtered)

    def _build_pagination(
        self,
        request: SearchRequest,
        results: tuple[HotelResult, ...],
        pagination_data: dict[str, Any],
    ) -> Pagination:
        total_results = _coerce_int(pagination_data.get("nbResultsTotal"))
        total_pages = None
        if total_results is not None and request.limit > 0:
            total_pages = ceil(total_results / request.limit)
        return Pagination(
            page=request.page,
            limit=request.limit,
            offset=request.offset,
            total_results=total_results,
            total_pages=total_pages,
            results_returned=len(results),
        )

    def _normalize_result(self, raw: dict[str, Any]) -> HotelResult:
        basic = _as_dict(raw.get("basicPropertyData"))
        raw_location = _as_dict(raw.get("location"))
        basic_location = _as_dict(basic.get("location"))
        review = _as_dict(basic.get("reviewScore")) or _as_dict(basic.get("reviews"))
        display = _as_dict(_as_dict(raw.get("priceDisplayInfoIrene")).get("displayPrice"))
        amount = _as_dict(display.get("amountPerStay"))
        first_block = _first_item(raw.get("blocks"))
        first_block_price = _as_dict(first_block.get("finalPrice")) if isinstance(first_block, dict) else {}
        page_name = basic.get("pageName")

        photos: list[str] = []
        photo_main = _as_dict(_as_dict(_as_dict(basic.get("photos")).get("main")))
        for candidate in (
            _relative_url_to_absolute(_dig(photo_main, "highResUrl", "relativeUrl"), self.settings.base_url),
            _relative_url_to_absolute(_dig(photo_main, "lowResUrl", "relativeUrl"), self.settings.base_url),
            _relative_url_to_absolute(_dig(photo_main, "highResJpegUrl", "relativeUrl"), self.settings.base_url),
            _relative_url_to_absolute(_dig(photo_main, "lowResJpegUrl", "relativeUrl"), self.settings.base_url),
        ):
            if candidate and candidate not in photos:
                photos.append(candidate)

        lat = _coerce_float(
            raw.get("latitude")
            or basic.get("latitude")
            or basic_location.get("latitude")
        )
        lon = _coerce_float(
            raw.get("longitude")
            or basic.get("longitude")
            or basic_location.get("longitude")
        )
        coordinates = None
        if lat is not None and lon is not None:
            coordinates = Coordinates(latitude=lat, longitude=lon)

        price = _coerce_float(amount.get("amountUnformatted") or amount.get("amount") or amount.get("amountRounded"))
        if price is None:
            price = _coerce_float(first_block_price.get("amount"))
        currency = _none_if_empty(amount.get("currency")) or _none_if_empty(first_block_price.get("currency"))
        translation_price_display = _none_if_empty(_dig(display, "copy", "translation"))
        amount_price_display = _none_if_empty(amount.get("amount"))
        price_display = translation_price_display if _contains_digit(translation_price_display) else amount_price_display
        if price_display is None and price is not None and currency:
            price_display = f"{currency} {price:g}"

        url = _property_url(raw, basic, self.settings.base_url, page_name)
        accommodation_type = (
            _none_if_empty(raw.get("accommodationType"))
            or _none_if_empty(basic.get("accommodationTypeName"))
            or _none_if_empty(basic.get("accommodationTypeId"))
        )

        return HotelResult(
            property_id=str(basic.get("id") or raw.get("id") or ""),
            name=str(_dig(raw, "displayName", "text") or raw.get("name") or f"Property {basic.get('id', '')}").strip(),
            url=url,
            stars=_coerce_int(_dig(basic, "starRating", "value") or raw.get("stars")),
            review_score=_coerce_float(
                review.get("score")
                or review.get("totalScore")
                or raw.get("review_score")
            ),
            review_count=_coerce_int(
                review.get("reviewCount")
                or review.get("reviewsCount")
                or raw.get("review_count")
            ),
            address=_none_if_empty(basic_location.get("address")),
            city=_none_if_empty(basic_location.get("city")),
            distance_to_center=_none_if_empty(raw_location.get("mainDistance") or raw_location.get("displayLocation")),
            distance_to_center_meters=_coerce_int(raw_location.get("geoDistanceMeters")),
            price=price,
            currency=currency,
            price_display=price_display,
            accommodation_type=accommodation_type,
            coordinates=coordinates,
            photos=tuple(photos),
            raw=raw,
        )

    def _extract_first_path(self, payload: dict[str, Any], paths: tuple[tuple[str, ...], ...]) -> Any:
        for path in paths:
            value = _dig(payload, *path)
            if value is not None:
                return value
        return None

    @staticmethod
    def _ensure_ok(response: httpx.Response, action: str) -> None:
        if response.status_code >= 400:
            raise BookingClientError(
                f"Booking {action} failed with HTTP {response.status_code}."
            )


def _dig(value: Any, *path: str) -> Any:
    current = value
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _first_item(value: Any) -> dict[str, Any] | None:
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return None


def _property_url(
    raw: dict[str, Any],
    basic: dict[str, Any],
    base_url: str,
    page_name: Any,
) -> str | None:
    direct = _none_if_empty(raw.get("url")) or _none_if_empty(raw.get("webUrl"))
    if direct:
        return direct
    if isinstance(page_name, str) and page_name:
        country_code = _none_if_empty(_dig(basic, "location", "countryCode"))
        if "/" not in page_name and country_code:
            return f"{base_url.rstrip('/')}/hotel/{country_code.lower()}/{page_name}.html"
        return _relative_url_to_absolute(page_name, base_url)
    return None


def _relative_url_to_absolute(value: Any, base_url: str) -> str | None:
    text = _none_if_empty(value)
    if text is None:
        return None
    if text.startswith("http://") or text.startswith("https://"):
        return text
    if not text.startswith("/"):
        text = "/" + text
    return f"{base_url.rstrip('/')}{text}"


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _none_if_empty(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _contains_digit(value: str | None) -> bool:
    return value is not None and any(character.isdigit() for character in value)
