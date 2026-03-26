from __future__ import annotations

from datetime import date
from unittest import TestCase
from unittest.mock import patch
import json

from click.testing import CliRunner

from booking_cli.cli import cli
from booking_cli.models import Destination, HotelResult, Pagination, SearchRequest, SearchResponse


def _sample_response() -> SearchResponse:
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
        sort="price",
        limit=25,
        page=1,
    )
    destination = Destination.from_override(query="Paris", dest_id="-1456928", dest_type="city")
    result = HotelResult(
        property_id="123",
        name="Hotel Example Paris",
        url="https://www.booking.com/hotel/fr/example-paris.html",
        stars=4,
        review_score=8.8,
        review_count=1234,
        address="1 Rue Example",
        city="Paris",
        distance_to_center="550 m from center",
        distance_to_center_meters=550,
        price=210.0,
        currency="EUR",
        price_display="EUR 210",
        accommodation_type="hotel",
        coordinates=None,
        photos=("https://www.booking.com/images/example-high.jpg",),
        raw={"property": "raw"},
    )
    return SearchResponse(
        request=request,
        destination=destination,
        pagination=Pagination(page=1, limit=25, offset=0, total_results=87, total_pages=4, results_returned=1),
        results=(result,),
        endpoint="https://www.booking.com/dml/graphql",
        meta={"sort_applied": "price", "filters": {}, "search_meta": {}, "verification": {}},
        raw_response={"data": {"searchQueries": {"search": {"results": []}}}},
    )


class FakeClient:
    def __init__(self, settings) -> None:
        self.settings = settings

    def search(self, request: SearchRequest) -> SearchResponse:
        return _sample_response()

    def resolve_destination(self, query: str, *, limit: int = 5):
        return (
            Destination.from_override(query="Paris", dest_id="-1456928", dest_type="city"),
            Destination.from_override(query="Paris City Centre", dest_id="2281", dest_type="district"),
        )[:limit]

    def close(self) -> None:
        return None


class CliTests(TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_help_includes_examples(self) -> None:
        result = self.runner.invoke(cli, ["--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Examples:", result.output)
        self.assertIn('booking-cli search --destination "Paris"', result.output)

    def test_search_json_output(self) -> None:
        with patch("booking_cli.cli.BookingClient", FakeClient):
            result = self.runner.invoke(
                cli,
                [
                    "--json",
                    "search",
                    "--destination",
                    "Paris",
                    "--checkin",
                    "2026-04-01",
                    "--checkout",
                    "2026-04-03",
                    "--adults",
                    "2",
                    "--rooms",
                    "1",
                    "--sort",
                    "price",
                ],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        payload = json.loads(result.output)
        self.assertEqual(payload["query"]["destination"], "Paris")
        self.assertEqual(payload["results"][0]["name"], "Hotel Example Paris")

    def test_search_text_output(self) -> None:
        with patch("booking_cli.cli.BookingClient", FakeClient):
            result = self.runner.invoke(
                cli,
                [
                    "search",
                    "--destination",
                    "Paris",
                    "--checkin",
                    "2026-04-01",
                    "--checkout",
                    "2026-04-03",
                    "--adults",
                    "2",
                    "--rooms",
                    "1",
                ],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Booking CLI | Paris", result.output)
        self.assertIn("Hotel Example Paris", result.output)
        self.assertIn("https://www.booking.com/hotel/fr/example-paris.html", result.output)

    def test_resolve_destination_json_output(self) -> None:
        with patch("booking_cli.cli.BookingClient", FakeClient):
            result = self.runner.invoke(cli, ["--json", "resolve-destination", "--query", "Paris"])

        self.assertEqual(result.exit_code, 0, result.output)
        payload = json.loads(result.output)
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["results"][0]["dest_id"], "-1456928")

    def test_validation_requires_destination_or_override(self) -> None:
        result = self.runner.invoke(
            cli,
            [
                "search",
                "--checkin",
                "2026-04-01",
                "--checkout",
                "2026-04-03",
                "--adults",
                "2",
                "--rooms",
                "1",
            ],
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Provide --destination, or both --dest-id and --dest-type.", result.output)
