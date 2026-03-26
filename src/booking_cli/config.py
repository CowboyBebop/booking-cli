from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import os
import sys
import tomllib

from booking_cli.session import default_session_cache_path

DEFAULT_BASE_URL = "https://www.booking.com"
DEFAULT_AUTOCOMPLETE_URL = "https://accommodations.booking.com/autocomplete.json"
DEFAULT_LANGUAGE = "en-gb"
DEFAULT_CURRENCY = "EUR"
DEFAULT_TIMEOUT = 20.0
DEFAULT_SESSION_TTL_MINUTES = 10
DEFAULT_BROWSER_TIMEOUT = 45.0
DEFAULT_BROWSER_HEADLESS = True
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
)
DEFAULT_AID = 304142


class ConfigError(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class Settings:
    base_url: str = DEFAULT_BASE_URL
    autocomplete_url: str = DEFAULT_AUTOCOMPLETE_URL
    user_agent: str = DEFAULT_USER_AGENT
    language: str = DEFAULT_LANGUAGE
    currency: str = DEFAULT_CURRENCY
    adults: int = 1
    rooms: int = 1
    timeout: float = DEFAULT_TIMEOUT
    aid: int = DEFAULT_AID
    session_cache_path: Path = default_session_cache_path()
    session_ttl_minutes: int = DEFAULT_SESSION_TTL_MINUTES
    browser_bootstrap: bool = True
    browser_headless: bool = DEFAULT_BROWSER_HEADLESS
    browser_timeout: float = DEFAULT_BROWSER_TIMEOUT
    browser_channel: str | None = None
    browser_auto_install: bool = True

    @property
    def graphql_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/dml/graphql"

    @property
    def search_base_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/searchresults.{self.language}.html"


def config_path_from_sources(cli_config: str | None) -> Path | None:
    if cli_config:
        return Path(cli_config).expanduser()

    env_path = os.getenv("BOOKING_CLI_CONFIG")
    if env_path:
        return Path(env_path).expanduser()

    return None


def load_settings(config_path: Path | None) -> Settings:
    data: dict[str, Any] = {}
    if config_path is not None:
        if not config_path.exists():
            raise ConfigError(f"Config file not found: {config_path}")

        try:
            data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except TOMLDecodeError as exc:
            raise ConfigError(f"Config file is not valid TOML: {config_path}") from exc

    defaults = _as_dict(data.get("defaults", {}))

    return Settings(
        base_url=_env_or_value("BOOKING_CLI_BASE_URL", data.get("base_url"), DEFAULT_BASE_URL),
        autocomplete_url=_env_or_value(
            "BOOKING_CLI_AUTOCOMPLETE_URL",
            data.get("autocomplete_url"),
            DEFAULT_AUTOCOMPLETE_URL,
        ),
        user_agent=_env_or_value(
            "BOOKING_CLI_USER_AGENT",
            defaults.get("user_agent"),
            DEFAULT_USER_AGENT,
        ),
        language=_env_or_value("BOOKING_CLI_LANGUAGE", defaults.get("language"), DEFAULT_LANGUAGE),
        currency=_env_or_value("BOOKING_CLI_CURRENCY", defaults.get("currency"), DEFAULT_CURRENCY),
        adults=_env_or_int("BOOKING_CLI_ADULTS", defaults.get("adults"), 1),
        rooms=_env_or_int("BOOKING_CLI_ROOMS", defaults.get("rooms"), 1),
        timeout=_env_or_float("BOOKING_CLI_TIMEOUT", defaults.get("timeout"), DEFAULT_TIMEOUT),
        aid=_env_or_int("BOOKING_CLI_AID", data.get("aid"), DEFAULT_AID),
        session_cache_path=_env_or_path(
            "BOOKING_CLI_SESSION_CACHE",
            data.get("session_cache"),
            default_session_cache_path(),
        ),
        session_ttl_minutes=_env_or_int(
            "BOOKING_CLI_SESSION_TTL_MINUTES",
            data.get("session_ttl_minutes"),
            DEFAULT_SESSION_TTL_MINUTES,
        ),
        browser_bootstrap=_env_or_bool(
            "BOOKING_CLI_BROWSER_BOOTSTRAP",
            data.get("browser_bootstrap"),
            True,
        ),
        browser_headless=_env_or_bool(
            "BOOKING_CLI_BROWSER_HEADLESS",
            data.get("browser_headless"),
            DEFAULT_BROWSER_HEADLESS,
        ),
        browser_timeout=_env_or_float(
            "BOOKING_CLI_BROWSER_TIMEOUT",
            data.get("browser_timeout"),
            DEFAULT_BROWSER_TIMEOUT,
        ),
        browser_channel=_env_or_optional_value(
            "BOOKING_CLI_BROWSER_CHANNEL",
            data.get("browser_channel"),
            _default_browser_channel(),
        ),
        browser_auto_install=_env_or_bool(
            "BOOKING_CLI_BROWSER_AUTO_INSTALL",
            data.get("browser_auto_install"),
            True,
        ),
    )


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    raise ConfigError("Config [defaults] section must be a table.")


def _env_or_value(name: str, config_value: Any, fallback: str) -> str:
    value = os.getenv(name)
    if value is not None:
        return value
    if config_value is not None:
        return str(config_value)
    return fallback


def _env_or_int(name: str, config_value: Any, fallback: int) -> int:
    value = os.getenv(name)
    if value is not None:
        return _parse_int(name, value)
    if config_value is not None:
        return _parse_int(name, config_value)
    return fallback


def _env_or_float(name: str, config_value: Any, fallback: float) -> float:
    value = os.getenv(name)
    if value is not None:
        return _parse_float(name, value)
    if config_value is not None:
        return _parse_float(name, config_value)
    return fallback


def _parse_int(name: str, value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be an integer.") from exc


def _parse_float(name: str, value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be a number.") from exc


def _env_or_path(name: str, config_value: Any, fallback: Path) -> Path:
    value = os.getenv(name)
    if value is not None:
        return Path(value).expanduser()
    if config_value is not None:
        return Path(str(config_value)).expanduser()
    return fallback


def _env_or_bool(name: str, config_value: Any, fallback: bool) -> bool:
    value = os.getenv(name)
    if value is not None:
        return _parse_bool(name, value)
    if config_value is not None:
        return _parse_bool(name, config_value)
    return fallback


def _env_or_optional_value(name: str, config_value: Any, fallback: str | None) -> str | None:
    value = os.getenv(name)
    if value is not None:
        normalized = value.strip()
        return normalized or None
    if config_value is not None:
        normalized = str(config_value).strip()
        return normalized or None
    return fallback


def _parse_bool(name: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be a boolean.")


def _default_browser_channel() -> str | None:
    if os.name == "nt":
        return "msedge"
    if sys.platform == "darwin":
        return "chrome"
    return None


try:
    from tomllib import TOMLDecodeError
except ImportError:  # pragma: no cover
    TOMLDecodeError = ValueError
