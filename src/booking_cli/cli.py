from __future__ import annotations

from datetime import date
from pathlib import Path
from textwrap import dedent
import json

import click

from booking_cli import __version__
from booking_cli.client import BookingBlockedError, BookingClient, BookingClientError
from booking_cli.config import ConfigError, config_path_from_sources, load_settings
from booking_cli.formatter import render_destinations, render_search
from booking_cli.models import SearchRequest

SORT_CHOICES = ("default", "price", "review-score", "distance", "stars", "name")


@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
    epilog=dedent(
        """\
        \b
        Examples:
          booking-cli search --destination "Paris" --checkin 2026-04-01 --checkout 2026-04-03 --adults 2 --rooms 1
          booking-cli --json search --destination "Paris" --checkin 2026-04-01 --checkout 2026-04-03 --adults 2 --rooms 1
          booking-cli resolve-destination --query "Paris"
        """
    ),
)
@click.option(
    "--config",
    "config_arg",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Optional TOML config file.",
)
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    help="Emit stable JSON suitable for scripts and agents.",
)
@click.option(
    "--out",
    "output_path",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write final output to a file instead of stdout.",
)
@click.version_option(__version__, prog_name="booking-cli")
@click.pass_context
def cli(ctx: click.Context, config_arg: Path | None, output_json: bool, output_path: Path | None) -> None:
    """Search Booking.com hotels without exposing GraphQL or CSRF details."""
    try:
        settings = load_settings(config_path_from_sources(str(config_arg) if config_arg else None))
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    ctx.ensure_object(dict)
    ctx.obj["settings"] = settings
    ctx.obj["output_json"] = output_json
    ctx.obj["output_path"] = output_path


@cli.command(
    epilog=dedent(
        """\
        \b
        Examples:
          booking-cli search --destination "Paris" --checkin 2026-04-01 --checkout 2026-04-03 --adults 2 --rooms 1
          booking-cli search --destination "Rome" --checkin 2026-05-10 --checkout 2026-05-13 --adults 2 --rooms 1 --sort price --limit 15
          booking-cli --json search --destination "Prague" --checkin 2026-06-02 --checkout 2026-06-05 --adults 2 --rooms 1 --raw
        """
    )
)
@click.option("--destination", type=str, help="Destination city, district, landmark, or airport.")
@click.option("--checkin", type=str, help="Check-in date in YYYY-MM-DD.")
@click.option("--checkout", type=str, help="Check-out date in YYYY-MM-DD.")
@click.option("--adults", type=int, help="Number of adults.")
@click.option("--rooms", type=int, help="Number of rooms.")
@click.option("--children", type=int, default=0, show_default=True, help="Number of children.")
@click.option(
    "--child-age",
    "child_ages",
    type=int,
    multiple=True,
    help="Child age. Repeat once per child.",
)
@click.option("--currency", type=str, help="Response currency, for example EUR or USD.")
@click.option("--language", type=str, help="Booking language/locale, for example en-gb.")
@click.option(
    "--sort",
    type=click.Choice(SORT_CHOICES, case_sensitive=False),
    help="Local sort order: default, price, review-score, distance, stars, or name.",
)
@click.option("--limit", type=int, default=25, show_default=True, help="Results per page, up to 100.")
@click.option("--page", type=int, default=1, show_default=True, help="1-based page number.")
@click.option("--min-review-score", type=float, help="Filter out properties below this review score.")
@click.option("--stars", type=int, multiple=True, help="Filter by star rating. Repeatable.")
@click.option("--dest-id", type=str, help="Advanced override for Booking destination ID.")
@click.option("--dest-type", type=str, help="Advanced override for Booking destination type.")
@click.option("--raw", is_flag=True, help="Print the raw GraphQL response instead of formatted results.")
@click.pass_context
def search(
    ctx: click.Context,
    destination: str | None,
    checkin: str | None,
    checkout: str | None,
    adults: int | None,
    rooms: int | None,
    children: int,
    child_ages: tuple[int, ...],
    currency: str | None,
    language: str | None,
    sort: str | None,
    limit: int,
    page: int,
    min_review_score: float | None,
    stars: tuple[int, ...],
    dest_id: str | None,
    dest_type: str | None,
    raw: bool,
) -> None:
    """Search for hotels with normal user input."""
    settings = ctx.obj["settings"]
    request = _build_search_request(
        destination=destination,
        checkin=checkin,
        checkout=checkout,
        adults=adults if adults is not None else settings.adults,
        rooms=rooms if rooms is not None else settings.rooms,
        children=children,
        child_ages=child_ages,
        currency=(currency or settings.currency).upper(),
        language=(language or settings.language).lower(),
        sort=(sort or "default").lower(),
        limit=limit,
        page=page,
        min_review_score=min_review_score,
        stars=stars,
        dest_id=dest_id,
        dest_type=dest_type,
        raw=raw,
    )

    client = BookingClient(settings)
    try:
        response = client.search(request)
    except (BookingClientError, BookingBlockedError) as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        client.close()

    if raw:
        _emit_output(ctx, json.dumps(response.raw_response, indent=2, sort_keys=True), nl=True)
        return

    if ctx.obj["output_json"]:
        _emit_output(ctx, json.dumps(response.to_dict(), indent=2, sort_keys=True), nl=True)
        return

    _emit_output(ctx, render_search(response), nl=False)


@cli.command(
    name="resolve-destination",
    epilog=dedent(
        """\
        \b
        Examples:
          booking-cli resolve-destination --query "Paris"
          booking-cli --json resolve-destination --query "Mallorca" --limit 3
        """
    ),
)
@click.option("--query", required=True, type=str, help="Destination text to resolve.")
@click.option("--limit", type=int, default=5, show_default=True, help="Maximum matches to show.")
@click.pass_context
def resolve_destination(ctx: click.Context, query: str, limit: int) -> None:
    """Inspect Booking destination matches for a query string."""
    client = BookingClient(ctx.obj["settings"])
    try:
        destinations = client.resolve_destination(query, limit=limit)
    except BookingClientError as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        client.close()

    if ctx.obj["output_json"]:
        payload = {
            "query": query,
            "count": len(destinations),
            "results": [item.to_dict() for item in destinations],
        }
        _emit_output(ctx, json.dumps(payload, indent=2, sort_keys=True), nl=True)
        return

    _emit_output(ctx, render_destinations(query, destinations), nl=False)


def main() -> None:
    cli(obj={})


def _emit_output(ctx: click.Context, content: str, *, nl: bool) -> None:
    output_path = ctx.obj.get("output_path")
    if output_path is None:
        click.echo(content, nl=nl)
        return
    final_content = content if not nl or content.endswith("\n") else f"{content}\n"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(final_content, encoding="utf-8")


def _build_search_request(
    *,
    destination: str | None,
    checkin: str | None,
    checkout: str | None,
    adults: int,
    rooms: int,
    children: int,
    child_ages: tuple[int, ...],
    currency: str,
    language: str,
    sort: str,
    limit: int,
    page: int,
    min_review_score: float | None,
    stars: tuple[int, ...],
    dest_id: str | None,
    dest_type: str | None,
    raw: bool,
) -> SearchRequest:
    if not destination and not (dest_id and dest_type):
        raise click.ClickException("Provide --destination, or both --dest-id and --dest-type.")
    if bool(dest_id) != bool(dest_type):
        raise click.ClickException("--dest-id and --dest-type must be provided together.")
    if not checkin:
        raise click.ClickException("Missing required option: --checkin")
    if not checkout:
        raise click.ClickException("Missing required option: --checkout")

    checkin_date = _parse_iso_date("checkin", checkin)
    checkout_date = _parse_iso_date("checkout", checkout)
    if checkout_date <= checkin_date:
        raise click.ClickException("--checkout must be after --checkin.")

    if adults < 1:
        raise click.ClickException("At least one adult is required.")
    if rooms < 1:
        raise click.ClickException("At least one room is required.")
    if adults < rooms:
        raise click.ClickException("--adults must be greater than or equal to --rooms.")
    if children < 0:
        raise click.ClickException("--children cannot be negative.")
    if len(child_ages) != children:
        raise click.ClickException("Provide exactly one --child-age for each child.")
    if any(age < 0 or age > 17 for age in child_ages):
        raise click.ClickException("--child-age values must be between 0 and 17.")

    if limit < 1 or limit > 100:
        raise click.ClickException("--limit must be between 1 and 100.")
    if page < 1:
        raise click.ClickException("--page must be at least 1.")

    offset = (page - 1) * limit
    if offset >= 1000 or (offset + limit) > 1000:
        raise click.ClickException("Booking pagination is capped at 1000 results. Lower --page or --limit.")

    if sort not in SORT_CHOICES:
        raise click.ClickException(f"--sort must be one of: {', '.join(SORT_CHOICES)}")

    normalized_stars = tuple(sorted(set(stars)))
    if any(star < 1 or star > 5 for star in normalized_stars):
        raise click.ClickException("--stars values must be between 1 and 5.")

    return SearchRequest(
        destination=destination,
        checkin=checkin_date,
        checkout=checkout_date,
        adults=adults,
        rooms=rooms,
        children=children,
        child_ages=tuple(child_ages),
        currency=currency,
        language=language,
        sort=sort,
        limit=limit,
        page=page,
        min_review_score=min_review_score,
        stars=normalized_stars,
        dest_id=dest_id,
        dest_type=dest_type,
        raw=raw,
    )


def _parse_iso_date(option_name: str, value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise click.ClickException(f"--{option_name} must be in YYYY-MM-DD format.") from exc
