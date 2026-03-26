from __future__ import annotations

from datetime import date
from re import Match
from typing import Any
from urllib.parse import urlencode
import json
import re

from booking_cli.models import Destination, SearchRequest

CSRF_PATTERNS = (
    re.compile(r"b_csrf_token:\s*'([^']+)'"),
    re.compile(r'"b_csrf_token"\s*:\s*"([^"]+)"'),
)

WAF_MARKERS = (
    "__challenge_",
    "challenge.js",
    "verify that you're not a robot",
    "JavaScript is disabled",
    "AwsWafIntegration",
)

RESULT_PATHS = (
    ("data", "searchQueries", "search", "results"),
    ("data", "searchQueries", "search", "search", "results"),
    ("data", "searchQueries", "search", "output", "results"),
    ("data", "searchQueries", "search", "data", "results"),
)

PAGINATION_PATHS = (
    ("data", "searchQueries", "search", "pagination"),
    ("data", "searchQueries", "search", "search", "pagination"),
    ("data", "searchQueries", "search", "output", "pagination"),
    ("data", "searchQueries", "search", "data", "pagination"),
)

SEARCH_META_PATHS = (
    ("data", "searchQueries", "search", "searchMeta"),
    ("data", "searchQueries", "search", "search", "searchMeta"),
    ("data", "searchQueries", "search", "output", "searchMeta"),
    ("data", "searchQueries", "search", "data", "searchMeta"),
)

FULL_SEARCH_QUERY = """
query FullSearch($input: SearchQueryInput!) {
  searchQueries {
    search(input: $input) {
      results {
        basicPropertyData {
          accommodationTypeId
          id
          location {
            address
            city
            countryCode
            __typename
          }
          pageName
          photos {
            main {
              highResUrl {
                relativeUrl
                __typename
              }
              lowResUrl {
                relativeUrl
                __typename
              }
              __typename
            }
            __typename
          }
          reviewScore: reviews {
            score: totalScore
            reviewCount: reviewsCount
            __typename
          }
          starRating {
            value
            __typename
          }
          __typename
        }
        displayName {
          text
          __typename
        }
        location {
          displayLocation
          mainDistance
          geoDistanceMeters
          __typename
        }
        priceDisplayInfoIrene {
          displayPrice {
            copy {
              translation
              __typename
            }
            amountPerStay {
              amount
              amountRounded
              amountUnformatted
              currency
              __typename
            }
            __typename
          }
          __typename
        }
        blocks {
          finalPrice {
            amount
            currency
            __typename
          }
          originalPrice {
            amount
            currency
            __typename
          }
          __typename
        }
        description {
          text
          __typename
        }
        __typename
      }
      __typename
    }
    __typename
  }
}
""".strip()


def build_autocomplete_payload(query: str, *, language: str, aid: int, size: int = 5) -> str:
    return (
        "{"
        f'"query":"{_escape_json_string(query)}",'
        '"pageview_id":"",'
        f'"aid":{aid},'
        f'"language":"{_escape_json_string(language)}",'
        f'"size":{size}'
        "}"
    )


def build_search_url(
    *,
    base_url: str,
    language: str,
    aid: int,
    request: SearchRequest,
    destination: Destination,
) -> str:
    params = {
        "ss": destination.value,
        "checkin": request.checkin.isoformat(),
        "checkout": request.checkout.isoformat(),
        "group_adults": request.adults,
        "group_children": request.children,
        "no_rooms": request.rooms,
        "dest_id": destination.dest_id,
        "dest_type": destination.dest_type,
        "lang": language,
        "aid": aid,
        "sb": 1,
        "src": "searchresults",
        "sb_travel_purpose": "leisure",
        "offset": request.offset,
    }
    if request.currency:
        params["selected_currency"] = request.currency
    return f"{base_url.rstrip('/')}/searchresults.{language}.html?{urlencode(params)}"


def build_graphql_payload(request: SearchRequest, destination: Destination) -> dict[str, Any]:
    return {
        "operationName": "FullSearch",
        "variables": {
            "input": {
                "dates": {
                    "checkin": request.checkin.isoformat(),
                    "checkout": request.checkout.isoformat(),
                },
                "location": {
                    "searchString": destination.value,
                    "destType": destination.dest_type,
                    "destId": destination.dest_id,
                },
                "configuration": {
                    "rooms": build_room_configuration(
                        adults=request.adults,
                        children_ages=request.child_ages,
                        rooms=request.rooms,
                    ),
                    "searchConfig": {
                        "doAvailabilityCheck": False,
                        "showUnavailableProperties": False,
                    },
                },
                "pagination": {
                    "rowsPerPage": request.limit,
                    "offset": request.offset,
                },
            },
        },
        "extensions": {},
        "query": FULL_SEARCH_QUERY,
    }


def build_browser_headers(*, user_agent: str, language: str) -> dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": _accept_language(language),
    }


def build_autocomplete_headers(*, user_agent: str, language: str) -> dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": _accept_language(language),
        "Origin": "https://www.booking.com",
        "Referer": "https://www.booking.com/",
        "Content-Type": "text/plain;charset=UTF-8",
    }


def build_graphql_headers(
    *,
    user_agent: str,
    language: str,
    csrf_token: str,
    referer: str,
) -> dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Accept": "*/*",
        "Accept-Language": _accept_language(language),
        "Content-Type": "application/json",
        "Origin": "https://www.booking.com",
        "Referer": referer,
        "X-Booking-CSRF-Token": csrf_token,
    }


def extract_csrf_token(html: str) -> str | None:
    for pattern in CSRF_PATTERNS:
        match = pattern.search(html)
        if match is not None:
            return _match_group(match, 1)
    return None


def extract_embedded_search_response(html: str) -> dict[str, Any] | None:
    property_name = '"searchQueries"'
    property_index = html.find(property_name)
    if property_index < 0:
        return None

    value_start = html.find("{", property_index + len(property_name))
    if value_start < 0:
        return None

    value_end = _find_matching_brace(html, value_start)
    if value_end < 0:
        return None

    fragment = "{" + html[property_index:value_end + 1] + "}"
    try:
        payload = json.loads(fragment)
    except json.JSONDecodeError:
        return None

    search_queries = payload.get("searchQueries")
    if not isinstance(search_queries, dict):
        return None

    for key, value in search_queries.items():
        if key.startswith("search(") and isinstance(value, dict):
            return {"data": {"searchQueries": {"search": value}}}
    return None


def is_waf_challenge(html: str) -> bool:
    if extract_embedded_search_response(html) is not None:
        return False
    lowered = html.lower()
    return any(marker.lower() in lowered for marker in WAF_MARKERS)


def build_room_configuration(*, adults: int, children_ages: tuple[int, ...], rooms: int) -> list[dict[str, Any]]:
    layout = [{"numberOfAdults": 1, "childrenAges": []} for _ in range(rooms)]
    remaining_adults = adults - rooms
    room_index = 0
    while remaining_adults > 0:
        layout[room_index]["numberOfAdults"] += 1
        remaining_adults -= 1
        room_index = (room_index + 1) % rooms

    for index, age in enumerate(children_ages):
        layout[index % rooms]["childrenAges"].append(age)

    return layout


def clamp_checkout_after_checkin(checkin: date, checkout: date) -> None:
    if checkout <= checkin:
        raise ValueError("checkout must be after checkin")


def _accept_language(language: str) -> str:
    normalized = language.replace("_", "-")
    primary = normalized.split("-", 1)[0]
    if primary == normalized:
        return f"{normalized},en;q=0.8"
    return f"{normalized},{primary};q=0.9,en;q=0.8"


def _escape_json_string(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def _match_group(match: Match[str], group: int) -> str | None:
    value = match.group(group)
    return value if value else None


def _find_matching_brace(text: str, start_index: int) -> int:
    depth = 0
    in_string = False
    escaped = False

    for index in range(start_index, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
            continue
        if char == "}":
            depth -= 1
            if depth == 0:
                return index

    return -1
