from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(slots=True, frozen=True)
class Coordinates:
    latitude: float
    longitude: float

    def to_dict(self) -> dict[str, float]:
        return {"latitude": self.latitude, "longitude": self.longitude}


@dataclass(slots=True, frozen=True)
class Destination:
    query: str
    label: str
    value: str
    dest_id: str
    dest_type: str
    country_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    roundtrip: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_autocomplete(cls, query: str, raw: dict[str, Any]) -> "Destination":
        return cls(
            query=query,
            label=str(raw.get("label") or raw.get("value") or query),
            value=str(raw.get("value") or raw.get("label1") or query),
            dest_id=str(raw.get("dest_id", "")),
            dest_type=str(raw.get("dest_type", "")),
            country_code=_none_if_empty(raw.get("cc1")),
            latitude=_coerce_float(raw.get("latitude")),
            longitude=_coerce_float(raw.get("longitude")),
            roundtrip=_none_if_empty(raw.get("roundtrip")),
            raw=dict(raw),
        )

    @classmethod
    def from_override(
        cls,
        *,
        query: str,
        dest_id: str,
        dest_type: str,
    ) -> "Destination":
        value = query or dest_id
        return cls(
            query=query or value,
            label=value,
            value=value,
            dest_id=dest_id,
            dest_type=dest_type,
            raw={},
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "query": self.query,
            "label": self.label,
            "value": self.value,
            "dest_id": self.dest_id,
            "dest_type": self.dest_type,
        }
        if self.country_code:
            data["country_code"] = self.country_code
        if self.latitude is not None and self.longitude is not None:
            data["coordinates"] = {"latitude": self.latitude, "longitude": self.longitude}
        if self.roundtrip:
            data["roundtrip"] = self.roundtrip
        return data


@dataclass(slots=True, frozen=True)
class SearchRequest:
    destination: str | None
    checkin: date
    checkout: date
    adults: int
    rooms: int
    children: int
    child_ages: tuple[int, ...]
    currency: str
    language: str
    sort: str
    limit: int
    page: int
    min_review_score: float | None = None
    stars: tuple[int, ...] = field(default_factory=tuple)
    dest_id: str | None = None
    dest_type: str | None = None
    raw: bool = False

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.limit

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "destination": self.destination,
            "checkin": self.checkin.isoformat(),
            "checkout": self.checkout.isoformat(),
            "adults": self.adults,
            "rooms": self.rooms,
            "children": self.children,
            "child_ages": list(self.child_ages),
            "currency": self.currency,
            "language": self.language,
            "sort": self.sort,
            "page": self.page,
            "limit": self.limit,
            "offset": self.offset,
        }
        if self.min_review_score is not None:
            data["min_review_score"] = self.min_review_score
        if self.stars:
            data["stars"] = list(self.stars)
        if self.dest_id:
            data["dest_id"] = self.dest_id
        if self.dest_type:
            data["dest_type"] = self.dest_type
        return data


@dataclass(slots=True, frozen=True)
class Pagination:
    page: int
    limit: int
    offset: int
    total_results: int | None = None
    total_pages: int | None = None
    results_returned: int = 0

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "page": self.page,
            "limit": self.limit,
            "offset": self.offset,
            "results_returned": self.results_returned,
        }
        if self.total_results is not None:
            data["total_results"] = self.total_results
        if self.total_pages is not None:
            data["total_pages"] = self.total_pages
        return data


@dataclass(slots=True, frozen=True)
class HotelResult:
    property_id: str
    name: str
    url: str | None
    stars: int | None
    review_score: float | None
    review_count: int | None
    address: str | None
    city: str | None
    distance_to_center: str | None
    distance_to_center_meters: int | None
    price: float | None
    currency: str | None
    price_display: str | None
    accommodation_type: str | None
    coordinates: Coordinates | None
    photos: tuple[str, ...]
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, include_raw: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "property_id": self.property_id,
            "name": self.name,
            "url": self.url,
            "stars": self.stars,
            "review_score": self.review_score,
            "review_count": self.review_count,
            "address": self.address,
            "city": self.city,
            "distance_to_center": self.distance_to_center,
            "distance_to_center_meters": self.distance_to_center_meters,
            "price": self.price,
            "currency": self.currency,
            "price_display": self.price_display,
            "accommodation_type": self.accommodation_type,
            "coordinates": self.coordinates.to_dict() if self.coordinates else None,
            "photos": list(self.photos),
        }
        if include_raw:
            data["raw"] = self.raw
        return data


@dataclass(slots=True, frozen=True)
class SearchResponse:
    request: SearchRequest
    destination: Destination
    pagination: Pagination
    results: tuple[HotelResult, ...]
    endpoint: str
    meta: dict[str, Any]
    raw_response: dict[str, Any] | None = None

    def to_dict(self, include_raw: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "query": self.request.to_dict(),
            "pagination": self.pagination.to_dict(),
            "results": [item.to_dict(include_raw=include_raw) for item in self.results],
            "meta": {
                **self.meta,
                "destination": self.destination.to_dict(),
                "endpoint": self.endpoint,
            },
        }
        if include_raw and self.raw_response is not None:
            payload["raw"] = self.raw_response
        return payload


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _none_if_empty(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
