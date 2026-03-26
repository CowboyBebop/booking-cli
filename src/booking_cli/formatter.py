from __future__ import annotations

from io import StringIO

from rich.console import Console
from rich.table import Table

from booking_cli.models import Destination, HotelResult, SearchResponse


def render_search(response: SearchResponse) -> str:
    console = _console()
    console.print(
        f"Booking CLI | {response.destination.label} | "
        f"{response.request.checkin.isoformat()} -> {response.request.checkout.isoformat()}"
    )
    console.print(
        f"Guests: {response.request.adults} adult(s), {response.request.children} child(ren) | "
        f"Rooms: {response.request.rooms} | Sort: {response.request.sort}"
    )

    if not response.results:
        console.print("No hotels matched the current query and filters.")
        return console.file.getvalue()

    table = Table(show_header=True, header_style="bold")
    table.add_column("Hotel", overflow="fold")
    table.add_column("City / Area", overflow="fold")
    table.add_column("Price", justify="right", no_wrap=True)
    table.add_column("Rating", justify="right", no_wrap=True)
    table.add_column("Stars", justify="center", no_wrap=True)
    table.add_column("Distance", no_wrap=True)
    table.add_column("Type", overflow="fold")
    table.add_column("URL", overflow="fold")

    for item in response.results:
        table.add_row(
            item.name,
            _city_area(item),
            item.price_display or _price_fallback(item),
            _rating(item),
            str(item.stars) if item.stars is not None else "-",
            item.distance_to_center or "-",
            item.accommodation_type or "-",
            item.url or "-",
        )

    console.print(table)
    if response.pagination.total_results is not None:
        console.print(
            f"Showing {response.pagination.results_returned} of {response.pagination.total_results} total results."
        )
    return console.file.getvalue()


def render_destinations(query: str, destinations: tuple[Destination, ...]) -> str:
    console = _console()
    console.print(f'Booking CLI destination matches for "{query}"')
    table = Table(show_header=True, header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("Label", overflow="fold")
    table.add_column("Type", no_wrap=True)
    table.add_column("Dest ID", no_wrap=True)
    table.add_column("Country", no_wrap=True)

    for index, item in enumerate(destinations, start=1):
        table.add_row(
            str(index),
            item.label,
            item.dest_type,
            item.dest_id,
            item.country_code or "-",
        )

    console.print(table)
    return console.file.getvalue()


def _console() -> Console:
    return Console(file=StringIO(), force_terminal=False, color_system=None, width=180)


def _city_area(item: HotelResult) -> str:
    if item.city and item.address:
        return f"{item.city} | {item.address}"
    if item.city:
        return item.city
    if item.address:
        return item.address
    return "-"


def _price_fallback(item: HotelResult) -> str:
    if item.price is None or item.currency is None:
        return "-"
    return f"{item.currency} {item.price:g}"


def _rating(item: HotelResult) -> str:
    if item.review_score is None:
        return "-"
    if item.review_count is None:
        return f"{item.review_score:.1f}"
    return f"{item.review_score:.1f} ({item.review_count})"
