from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest import TestCase
from urllib.parse import parse_qs, urlparse
import json

from booking_cli.graphql import (
    build_graphql_payload,
    build_search_url,
    extract_embedded_search_response,
    extract_csrf_token,
    is_waf_challenge,
)
from booking_cli.models import Destination, SearchRequest

FIXTURES = Path(__file__).parent / "fixtures"


class GraphqlHelperTests(TestCase):
    def test_extract_csrf_token(self) -> None:
        html = (FIXTURES / "search_page.html").read_text(encoding="utf-8")
        self.assertEqual(extract_csrf_token(html), "csrf-token-123")

    def test_detect_waf_challenge(self) -> None:
        html = (FIXTURES / "waf_challenge.html").read_text(encoding="utf-8")
        self.assertTrue(is_waf_challenge(html))

    def test_extract_embedded_search_response(self) -> None:
        html = _embedded_search_html()
        payload = extract_embedded_search_response(html)

        self.assertIsNotNone(payload)
        self.assertEqual(payload["data"]["searchQueries"]["search"]["pagination"]["nbResultsTotal"], 87)
        self.assertEqual(payload["data"]["searchQueries"]["search"]["results"][0]["displayName"]["text"], "Hotel Example Paris")

    def test_embedded_search_data_is_not_misclassified_as_waf(self) -> None:
        html = _embedded_search_html().replace("<body>", "<body>__challenge_ challenge.js ")
        self.assertFalse(is_waf_challenge(html))

    def test_build_search_url(self) -> None:
        request = SearchRequest(
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
            page=2,
        )
        destination = Destination.from_override(query="Paris", dest_id="-1456928", dest_type="city")
        url = build_search_url(
            base_url="https://www.booking.com",
            language="en-gb",
            aid=304142,
            request=request,
            destination=destination,
        )

        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/searchresults.en-gb.html")
        self.assertEqual(params["ss"], ["Paris"])
        self.assertEqual(params["dest_id"], ["-1456928"])
        self.assertEqual(params["group_adults"], ["2"])
        self.assertEqual(params["offset"], ["25"])

    def test_build_graphql_payload(self) -> None:
        request = SearchRequest(
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
            limit=10,
            page=1,
        )
        destination = Destination.from_override(query="Paris", dest_id="-1456928", dest_type="city")
        payload = build_graphql_payload(request, destination)

        self.assertEqual(payload["operationName"], "FullSearch")
        self.assertEqual(payload["variables"]["input"]["location"]["destId"], "-1456928")
        self.assertEqual(payload["variables"]["input"]["pagination"]["rowsPerPage"], 10)
        self.assertEqual(payload["variables"]["input"]["configuration"]["rooms"][0]["numberOfAdults"], 2)


def _embedded_search_html() -> str:
    payload = {
        "searchQueries": {
            "__typename": "SearchQueries",
            'search({"input":{}})': {
                "__typename": "SearchQueryOutput",
                "pagination": {
                    "__typename": "SearchResultsPagination",
                    "nbResultsPerPage": 25,
                    "nbResultsTotal": 87,
                },
                "results": [
                    {
                        "__typename": "SearchResultProperty",
                        "basicPropertyData": {
                            "__typename": "BasicPropertyData",
                            "accommodationTypeId": 204,
                            "id": 123,
                            "location": {
                                "__typename": "Location",
                                "address": "1 Rue Example",
                                "city": "Paris",
                                "countryCode": "fr",
                            },
                            "pageName": "example-paris",
                            "photos": {
                                "__typename": "PropertyPhotos",
                                "main": {
                                    "__typename": "Photo",
                                    "highResUrl": {
                                        "__typename": "PhotoResource",
                                        "relativeUrl": "/images/example-high.jpg",
                                    },
                                    "lowResUrl": {
                                        "__typename": "PhotoResource",
                                        "relativeUrl": "/images/example-low.jpg",
                                    },
                                },
                            },
                            "reviews": {
                                "__typename": "Reviews",
                                "totalScore": 8.8,
                                "reviewsCount": 1234,
                            },
                            "starRating": {
                                "__typename": "StarRating",
                                "value": 4,
                            },
                        },
                        "displayName": {
                            "__typename": "TextWithTranslationTag",
                            "text": "Hotel Example Paris",
                        },
                        "location": {
                            "__typename": "SearchResultsPropertyLocation",
                            "displayLocation": "Paris",
                            "mainDistance": "550 m from centre",
                            "geoDistanceMeters": 550,
                        },
                        "priceDisplayInfoIrene": {
                            "__typename": "PriceDisplayInfoIrene",
                            "displayPrice": {
                                "__typename": "PriceDisplayAggregatedIrene",
                                "copy": {
                                    "__typename": "TranslationTag",
                                    "translation": "Total",
                                },
                                "amountPerStay": {
                                    "__typename": "PriceDisplayIrene",
                                    "amount": "EUR 210",
                                    "amountRounded": "EUR 210",
                                    "amountUnformatted": 210,
                                    "currency": "EUR",
                                },
                            },
                        },
                        "blocks": [
                            {
                                "__typename": "Block",
                                "finalPrice": {
                                    "__typename": "Price",
                                    "amount": 210,
                                    "currency": "EUR",
                                },
                                "originalPrice": {
                                    "__typename": "Price",
                                    "amount": 250,
                                    "currency": "EUR",
                                },
                            }
                        ],
                        "description": {
                            "__typename": "Description",
                            "text": "Sample description",
                        },
                    }
                ],
            },
        }
    }
    return f"<html><body><script>{json.dumps(payload, separators=(',', ':'))}</script></body></html>"
