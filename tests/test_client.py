from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path
from unittest import TestCase
import json
import tempfile

import httpx

from booking_cli.client import BookingBlockedError, BookingClient
from booking_cli.config import Settings
from booking_cli.models import SearchRequest
from booking_cli.session import SessionCache, SessionCookie, SessionState

FIXTURES = Path(__file__).parent / "fixtures"


def _response(
    *,
    method: str,
    url: str,
    status_code: int = 200,
    text: str | None = None,
    payload: dict | None = None,
) -> httpx.Response:
    request = httpx.Request(method, url)
    if payload is not None:
        return httpx.Response(
            status_code,
            text=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            request=request,
        )
    return httpx.Response(status_code, text=text or "", request=request)


class FakeHttpClient:
    def __init__(self, *, get_responses: list[httpx.Response], post_responses: list[httpx.Response]) -> None:
        self.get_responses = list(get_responses)
        self.post_responses = list(post_responses)
        self.cookies = httpx.Cookies()
        self.get_calls: list[dict[str, object]] = []
        self.post_calls: list[dict[str, object]] = []

    def get(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        self.get_calls.append({"url": url, "headers": headers or {}})
        return self.get_responses.pop(0)

    def post(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        content: str | None = None,
        json: dict | None = None,
    ) -> httpx.Response:
        self.post_calls.append(
            {
                "url": url,
                "headers": headers or {},
                "content": content,
                "json": json,
            }
        )
        return self.post_responses.pop(0)

    def close(self) -> None:
        return None


class ClientTests(TestCase):
    def test_resolve_destination_parses_json(self) -> None:
        fake = FakeHttpClient(
            get_responses=[],
            post_responses=[
                _response(
                    method="POST",
                    url="https://accommodations.booking.com/autocomplete.json",
                    payload={
                        "results": [
                            {
                                "label": "Paris, Ile de France, France",
                                "value": "Paris",
                                "dest_id": "-1456928",
                                "dest_type": "city",
                                "cc1": "fr",
                                "latitude": 48.8566,
                                "longitude": 2.3522
                            }
                        ]
                    },
                )
            ],
        )
        client = BookingClient(Settings(), http_client=fake)
        results = client.resolve_destination("Paris")
        self.assertEqual(results[0].dest_id, "-1456928")
        self.assertEqual(results[0].country_code, "fr")

    def test_search_parses_fixture(self) -> None:
        html = (FIXTURES / "search_page.html").read_text(encoding="utf-8")
        payload = json.loads((FIXTURES / "graphql_search.json").read_text(encoding="utf-8"))
        fake = FakeHttpClient(
            get_responses=[
                _response(method="GET", url="https://www.booking.com/searchresults.en-gb.html", text=html),
                _response(method="GET", url="https://www.booking.com/searchresults.en-gb.html", text=html),
                _response(method="GET", url="https://www.booking.com/searchresults.en-gb.html", text=html),
            ],
            post_responses=[
                _response(method="POST", url="https://www.booking.com/dml/graphql?lang=en-gb", payload=payload)
            ],
        )
        client = BookingClient(Settings(), http_client=fake)
        response = client.search(_request())

        self.assertEqual(response.pagination.total_results, 87)
        self.assertEqual(len(response.results), 2)
        self.assertEqual(response.results[0].name, "Hotel Example Paris")
        self.assertEqual(response.results[0].price_display, "EUR 210")
        self.assertEqual(response.results[0].url, "https://www.booking.com/hotel/fr/example-paris.html")
        self.assertEqual(
            response.results[0].photos[0],
            "https://www.booking.com/images/example-high.jpg",
        )

    def test_search_prefers_embedded_search_html(self) -> None:
        fake = FakeHttpClient(
            get_responses=[
                _response(
                    method="GET",
                    url="https://www.booking.com/searchresults.en-gb.html",
                    text=_embedded_search_html(),
                )
            ],
            post_responses=[],
        )
        client = BookingClient(Settings(session_ttl_minutes=0), http_client=fake)
        response = client.search(_request())

        self.assertEqual(len(fake.post_calls), 0)
        self.assertEqual(response.pagination.total_results, 87)
        self.assertEqual(response.results[0].name, "Hotel Example Paris")
        self.assertEqual(response.meta["session"]["transport"], "embedded-html")

    def test_search_embedded_html_uses_amount_when_copy_is_generic(self) -> None:
        payload = json.loads((FIXTURES / "graphql_search.json").read_text(encoding="utf-8"))
        display_price = payload["data"]["searchQueries"]["search"]["results"][0]["priceDisplayInfoIrene"]["displayPrice"]
        display_price["copy"]["translation"] = "Total"
        display_price["amountPerStay"]["amount"] = "US$482.24"
        display_price["amountPerStay"]["amountUnformatted"] = 482.24366734611544
        display_price["amountPerStay"]["currency"] = "USD"

        fake = FakeHttpClient(
            get_responses=[
                _response(
                    method="GET",
                    url="https://www.booking.com/searchresults.en-gb.html",
                    text=_embedded_search_html(payload["data"]["searchQueries"]["search"]),
                )
            ],
            post_responses=[],
        )
        client = BookingClient(Settings(session_ttl_minutes=0), http_client=fake)
        response = client.search(_request())

        self.assertEqual(response.results[0].price_display, "US$482.24")
        self.assertEqual(response.results[0].currency, "USD")
        self.assertEqual(response.results[0].price, 482.24366734611544)

    def test_search_retries_once_after_403(self) -> None:
        html = (FIXTURES / "search_page.html").read_text(encoding="utf-8")
        payload = json.loads((FIXTURES / "graphql_search.json").read_text(encoding="utf-8"))
        fake = FakeHttpClient(
            get_responses=[
                _response(method="GET", url="https://www.booking.com/searchresults.en-gb.html", text=html),
                _response(method="GET", url="https://www.booking.com/searchresults.en-gb.html", text=html),
                _response(method="GET", url="https://www.booking.com/searchresults.en-gb.html", text=html),
                _response(method="GET", url="https://www.booking.com/searchresults.en-gb.html", text=html),
            ],
            post_responses=[
                _response(method="POST", url="https://www.booking.com/dml/graphql?lang=en-gb", status_code=403),
                _response(method="POST", url="https://www.booking.com/dml/graphql?lang=en-gb", payload=payload),
            ],
        )
        client = BookingClient(Settings(), http_client=fake)
        response = client.search(_request())
        self.assertEqual(len(response.results), 2)

    def test_search_detects_waf_challenge(self) -> None:
        html = (FIXTURES / "waf_challenge.html").read_text(encoding="utf-8")
        fake = FakeHttpClient(
            get_responses=[
                _response(method="GET", url="https://www.booking.com/searchresults.en-gb.html", text=html),
                _response(method="GET", url="https://www.booking.com/searchresults.en-gb.html", text=html),
            ],
            post_responses=[],
        )
        client = BookingClient(Settings(browser_bootstrap=False), http_client=fake)
        with self.assertRaises(BookingBlockedError):
            client.search(_request())

    def test_search_handles_missing_fields(self) -> None:
        html = (FIXTURES / "search_page.html").read_text(encoding="utf-8")
        payload = json.loads((FIXTURES / "graphql_search_missing_fields.json").read_text(encoding="utf-8"))
        fake = FakeHttpClient(
            get_responses=[
                _response(method="GET", url="https://www.booking.com/searchresults.en-gb.html", text=html),
                _response(method="GET", url="https://www.booking.com/searchresults.en-gb.html", text=html),
                _response(method="GET", url="https://www.booking.com/searchresults.en-gb.html", text=html),
            ],
            post_responses=[
                _response(method="POST", url="https://www.booking.com/dml/graphql?lang=en-gb", payload=payload)
            ],
        )
        client = BookingClient(Settings(), http_client=fake)
        response = client.search(_request())

        self.assertEqual(response.results[0].name, "Minimal Stay")
        self.assertEqual(response.results[0].url, "https://www.booking.com/hotel/fr/minimal.html")
        self.assertIsNone(response.results[0].price)

    def test_search_uses_cached_session_without_fetching_search_page(self) -> None:
        html = (FIXTURES / "search_page.html").read_text(encoding="utf-8")
        payload = json.loads((FIXTURES / "graphql_search.json").read_text(encoding="utf-8"))
        fake = FakeHttpClient(
            get_responses=[
                _response(method="GET", url="https://www.booking.com/searchresults.en-gb.html", text=html)
            ],
            post_responses=[
                _response(method="POST", url="https://www.booking.com/dml/graphql?lang=en-gb", payload=payload)
            ],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            cache = SessionCache(Path(temp_dir) / "session.json")
            cache.save(
                SessionState(
                    csrf_token="cached-csrf-token",
                    cookies=(
                        SessionCookie(name="booking_session", value="cookie-123", domain=".booking.com"),
                    ),
                    updated_at=datetime.now(timezone.utc),
                    base_url="https://www.booking.com",
                    language="en-gb",
                    user_agent=Settings().user_agent,
                    source="browser:msedge",
                )
            )
            client = BookingClient(Settings(), http_client=fake, session_cache=cache)
            response = client.search(_request())

        self.assertEqual(len(fake.get_calls), 1)
        self.assertEqual(fake.post_calls[0]["headers"]["X-Booking-CSRF-Token"], "cached-csrf-token")
        self.assertEqual(fake.cookies.get("booking_session"), "cookie-123")
        self.assertTrue(response.meta["session"]["cache_hit"])
        self.assertEqual(response.meta["session"]["source"], "browser:msedge")

    def test_search_ignores_expired_cached_session(self) -> None:
        html = (FIXTURES / "search_page.html").read_text(encoding="utf-8")
        payload = json.loads((FIXTURES / "graphql_search.json").read_text(encoding="utf-8"))
        fake = FakeHttpClient(
            get_responses=[
                _response(method="GET", url="https://www.booking.com/searchresults.en-gb.html", text=html),
                _response(method="GET", url="https://www.booking.com/searchresults.en-gb.html", text=html),
                _response(method="GET", url="https://www.booking.com/searchresults.en-gb.html", text=html),
            ],
            post_responses=[
                _response(method="POST", url="https://www.booking.com/dml/graphql?lang=en-gb", payload=payload)
            ],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            cache = SessionCache(Path(temp_dir) / "session.json")
            cache.save(
                SessionState(
                    csrf_token="expired-csrf-token",
                    cookies=(),
                    updated_at=datetime.now(timezone.utc) - timedelta(minutes=11),
                    base_url="https://www.booking.com",
                    language="en-gb",
                    user_agent=Settings().user_agent,
                    source="browser:msedge",
                )
            )
            client = BookingClient(Settings(session_ttl_minutes=10), http_client=fake, session_cache=cache)
            response = client.search(_request())

        self.assertEqual(len(fake.get_calls), 3)
        self.assertFalse(response.meta["session"]["cache_hit"])
        self.assertEqual(response.meta["session"]["source"], "http")

    def test_search_uses_browser_bootstrap_after_waf_challenge(self) -> None:
        html = (FIXTURES / "waf_challenge.html").read_text(encoding="utf-8")
        search_page_html = (FIXTURES / "search_page.html").read_text(encoding="utf-8")
        payload = json.loads((FIXTURES / "graphql_search.json").read_text(encoding="utf-8"))
        fake = FakeHttpClient(
            get_responses=[
                _response(method="GET", url="https://www.booking.com/searchresults.en-gb.html", text=html),
                _response(method="GET", url="https://www.booking.com/searchresults.en-gb.html", text=html),
                _response(method="GET", url="https://www.booking.com/searchresults.en-gb.html", text=search_page_html),
            ],
            post_responses=[
                _response(method="POST", url="https://www.booking.com/dml/graphql?lang=en-gb", payload=payload)
            ],
        )

        def bootstrap(search_url: str, settings: Settings) -> SessionState:
            return SessionState(
                csrf_token="browser-csrf-token",
                cookies=(
                    SessionCookie(name="booking_session", value="cookie-123", domain=".booking.com"),
                ),
                updated_at=datetime.now(timezone.utc),
                base_url=settings.base_url,
                language=settings.language,
                user_agent=settings.user_agent,
                source="browser:stub",
            )

        client = BookingClient(Settings(), http_client=fake, browser_bootstrapper=bootstrap)
        response = client.search(_request())

        self.assertEqual(len(fake.get_calls), 3)
        self.assertEqual(fake.post_calls[0]["headers"]["X-Booking-CSRF-Token"], "browser-csrf-token")
        self.assertEqual(fake.cookies.get("booking_session"), "cookie-123")
        self.assertEqual(response.meta["session"]["source"], "browser:stub")


def _request() -> SearchRequest:
    return SearchRequest(
        destination="Paris",
        checkin=date(2026, 4, 1),
        checkout=date(2026, 4, 3),
        adults=2,
        rooms=1,
        children=0,
        child_ages=(),
        currency="EUR",
        language="en-gb",
        sort="default",
        limit=25,
        page=1,
        dest_id="-1456928",
        dest_type="city",
    )


def _embedded_search_html(search_payload: dict | None = None) -> str:
    if search_payload is None:
        search_payload = json.loads((FIXTURES / "graphql_search.json").read_text(encoding="utf-8"))["data"]["searchQueries"]["search"]
    payload = {
        "searchQueries": {
            "__typename": "SearchQueries",
            'search({"input":{}})': search_payload,
        }
    }
    return f"<html><body><script>{json.dumps(payload, separators=(',', ':'))}</script></body></html>"
