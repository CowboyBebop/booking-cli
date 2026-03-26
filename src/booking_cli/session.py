from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.cookiejar import Cookie
from pathlib import Path
from typing import Any
import json
import os
import sys

import httpx


@dataclass(slots=True, frozen=True)
class SessionCookie:
    name: str
    value: str
    domain: str
    path: str = "/"
    secure: bool = False
    expires: float | None = None
    http_only: bool = False

    @classmethod
    def from_http_cookie(cls, cookie: Cookie) -> "SessionCookie":
        return cls(
            name=cookie.name,
            value=cookie.value,
            domain=cookie.domain or "",
            path=cookie.path or "/",
            secure=bool(cookie.secure),
            expires=float(cookie.expires) if cookie.expires is not None else None,
            http_only=bool(cookie._rest.get("HttpOnly")),  # noqa: SLF001
        )

    @classmethod
    def from_playwright_cookie(cls, raw: dict[str, Any]) -> "SessionCookie":
        return cls(
            name=str(raw.get("name") or ""),
            value=str(raw.get("value") or ""),
            domain=str(raw.get("domain") or ""),
            path=str(raw.get("path") or "/"),
            secure=bool(raw.get("secure", False)),
            expires=_coerce_float(raw.get("expires")),
            http_only=bool(raw.get("httpOnly", False)),
        )

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SessionCookie":
        name = str(raw.get("name") or "").strip()
        value = str(raw.get("value") or "")
        domain = str(raw.get("domain") or "").strip()
        path = str(raw.get("path") or "/").strip() or "/"
        if not name or not domain:
            raise ValueError("Session cookies require name and domain.")
        return cls(
            name=name,
            value=value,
            domain=domain,
            path=path,
            secure=bool(raw.get("secure", False)),
            expires=_coerce_float(raw.get("expires")),
            http_only=bool(raw.get("http_only", False)),
        )

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expires is None:
            return False
        reference = now or datetime.now(timezone.utc)
        return reference.timestamp() >= self.expires

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "value": self.value,
            "domain": self.domain,
            "path": self.path,
            "secure": self.secure,
            "http_only": self.http_only,
        }
        if self.expires is not None:
            payload["expires"] = self.expires
        return payload


@dataclass(slots=True, frozen=True)
class SessionState:
    csrf_token: str
    cookies: tuple[SessionCookie, ...]
    updated_at: datetime
    base_url: str
    language: str
    user_agent: str
    source: str = "http"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SessionState":
        csrf_token = str(raw.get("csrf_token") or "").strip()
        updated_raw = str(raw.get("updated_at") or "").strip()
        base_url = str(raw.get("base_url") or "").strip()
        language = str(raw.get("language") or "").strip()
        user_agent = str(raw.get("user_agent") or "").strip()
        if not csrf_token or not updated_raw or not base_url or not language or not user_agent:
            raise ValueError("Session cache entry is incomplete.")

        updated_at = datetime.fromisoformat(updated_raw)
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)

        raw_cookies = raw.get("cookies", [])
        if not isinstance(raw_cookies, list):
            raise ValueError("Session cache cookies must be a list.")

        return cls(
            csrf_token=csrf_token,
            cookies=tuple(SessionCookie.from_dict(item) for item in raw_cookies if isinstance(item, dict)),
            updated_at=updated_at.astimezone(timezone.utc),
            base_url=base_url.rstrip("/"),
            language=language,
            user_agent=user_agent,
            source=str(raw.get("source") or "http"),
        )

    def applies_to(self, *, base_url: str, language: str, user_agent: str) -> bool:
        return (
            self.base_url == base_url.rstrip("/")
            and self.language == language
            and self.user_agent == user_agent
        )

    def is_fresh(self, ttl_minutes: int, *, now: datetime | None = None) -> bool:
        if ttl_minutes <= 0:
            return False
        reference = now or datetime.now(timezone.utc)
        return reference - self.updated_at < timedelta(minutes=ttl_minutes)

    def without_expired_cookies(self, *, now: datetime | None = None) -> "SessionState":
        reference = now or datetime.now(timezone.utc)
        return SessionState(
            csrf_token=self.csrf_token,
            cookies=tuple(cookie for cookie in self.cookies if not cookie.is_expired(reference)),
            updated_at=self.updated_at,
            base_url=self.base_url,
            language=self.language,
            user_agent=self.user_agent,
            source=self.source,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "csrf_token": self.csrf_token,
            "cookies": [cookie.to_dict() for cookie in self.cookies],
            "updated_at": self.updated_at.astimezone(timezone.utc).isoformat(),
            "base_url": self.base_url,
            "language": self.language,
            "user_agent": self.user_agent,
            "source": self.source,
        }


class SessionCache:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> SessionState | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        try:
            return SessionState.from_dict(payload)
        except ValueError:
            return None

    def save(self, state: SessionState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(state.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        _best_effort_private_permissions(self.path)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


class NullSessionCache(SessionCache):
    def __init__(self) -> None:
        super().__init__(Path("."))

    def load(self) -> SessionState | None:
        return None

    def save(self, state: SessionState) -> None:
        return None

    def clear(self) -> None:
        return None


def capture_http_session(
    *,
    client: httpx.Client | Any,
    csrf_token: str,
    base_url: str,
    language: str,
    user_agent: str,
    source: str,
) -> SessionState:
    cookies = tuple(_collect_http_cookies(client))
    return SessionState(
        csrf_token=csrf_token,
        cookies=cookies,
        updated_at=datetime.now(timezone.utc),
        base_url=base_url.rstrip("/"),
        language=language,
        user_agent=user_agent,
        source=source,
    )


def apply_session_to_http_client(client: httpx.Client | Any, state: SessionState) -> None:
    cookies = getattr(client, "cookies", None)
    if cookies is None:
        return
    clear = getattr(cookies, "clear", None)
    if callable(clear):
        clear()
    for cookie in state.without_expired_cookies().cookies:
        cookies.set(cookie.name, cookie.value, domain=cookie.domain, path=cookie.path)


def default_session_cache_path() -> Path:
    return default_state_dir() / "session.json"


def default_state_dir() -> Path:
    if os.name == "nt":
        return Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "booking-cli"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "booking-cli"
    return Path(os.getenv("XDG_STATE_HOME") or (Path.home() / ".local" / "state")) / "booking-cli"


def _collect_http_cookies(client: httpx.Client | Any) -> tuple[SessionCookie, ...]:
    cookies = getattr(client, "cookies", None)
    jar = getattr(cookies, "jar", None)
    if jar is None:
        return ()
    return tuple(SessionCookie.from_http_cookie(cookie) for cookie in jar)


def _best_effort_private_permissions(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        return


def _coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
