from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import subprocess
import sys
import time

from booking_cli.config import Settings
from booking_cli.graphql import extract_csrf_token, is_waf_challenge
from booking_cli.session import SessionCookie, SessionState


class BrowserBootstrapError(RuntimeError):
    pass


def bootstrap_browser_session(search_url: str, settings: Settings) -> SessionState:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserBootstrapError(
            "Automatic browser bootstrap requires Playwright. Install it with "
            f"`{_python_command()} -m pip install playwright`."
        ) from exc

    try:
        with sync_playwright() as playwright:
            browser, browser_name = _launch_browser(playwright, settings)
            try:
                context = browser.new_context(
                    locale=settings.language,
                    user_agent=settings.user_agent,
                )
                page = context.new_page()
                page.goto(search_url, wait_until="domcontentloaded", timeout=_timeout_ms(settings))
                csrf_token = _wait_for_csrf_token(page, timeout_seconds=settings.browser_timeout)
                if not csrf_token:
                    html = page.content()
                    if is_waf_challenge(html):
                        raise BrowserBootstrapError(
                            "Browser automation reached Booking, but the anti-bot challenge did not clear in time."
                        )
                    raise BrowserBootstrapError(
                        "Browser automation reached the search page, but no CSRF token was found."
                    )

                cookies = tuple(
                    SessionCookie.from_playwright_cookie(item)
                    for item in context.cookies([settings.base_url])
                    if isinstance(item, dict)
                )
            finally:
                browser.close()
    except PlaywrightError as exc:
        raise BrowserBootstrapError(str(exc)) from exc

    return SessionState(
        csrf_token=csrf_token,
        cookies=cookies,
        updated_at=datetime.now(timezone.utc),
        base_url=settings.base_url.rstrip("/"),
        language=settings.language,
        user_agent=settings.user_agent,
        source=f"browser:{browser_name}",
    )


def _launch_browser(playwright: Any, settings: Settings) -> tuple[Any, str]:
    candidates: list[tuple[str, dict[str, Any]]] = []
    if settings.browser_channel:
        candidates.append(
            (
                settings.browser_channel,
                {
                    "channel": settings.browser_channel,
                    "headless": settings.browser_headless,
                },
            )
        )
    elif settings.browser_headless:
        # Use Playwright's "chromium" channel so headless mode runs the real browser
        # instead of the separate headless shell.
        candidates.append(
            (
                "chromium",
                {
                    "channel": "chromium",
                    "headless": True,
                },
            )
        )
    candidates.append(("chromium", {"headless": settings.browser_headless}))

    last_error: Exception | None = None
    for browser_name, options in candidates:
        try:
            browser = playwright.chromium.launch(**options)
            return browser, browser_name
        except Exception as exc:  # pragma: no cover - depends on local browser install
            last_error = exc

    if settings.browser_auto_install:
        _install_playwright_browser()
        try:
            browser = playwright.chromium.launch(headless=settings.browser_headless)
            return browser, "chromium"
        except Exception as exc:  # pragma: no cover - depends on local browser install
            last_error = exc

    if last_error is None:
        raise BrowserBootstrapError("No browser launch candidate was available.")
    raise BrowserBootstrapError(str(last_error)) from last_error


def _install_playwright_browser() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise BrowserBootstrapError(
            "Automatic browser install failed."
            + (f" {stderr}" if stderr else "")
        )


def _wait_for_csrf_token(page: Any, *, timeout_seconds: float) -> str | None:
    deadline = time.monotonic() + max(timeout_seconds, 1.0)
    while time.monotonic() < deadline:
        token = _extract_token(page)
        if token:
            return token
        page.wait_for_timeout(500)
    return _extract_token(page)


def _extract_token(page: Any) -> str | None:
    try:
        value = page.evaluate(
            "() => window.booking?.b_csrf_token ?? globalThis.booking?.b_csrf_token ?? null"
        )
    except Exception:
        value = None
    if isinstance(value, str) and value.strip():
        return value.strip()
    try:
        return extract_csrf_token(page.content())
    except Exception:
        return None


def _timeout_ms(settings: Settings) -> int:
    return int(max(settings.browser_timeout, 1.0) * 1000)


def _python_command() -> str:
    executable = sys.executable or "python"
    if executable.lower().endswith("python.exe"):
        return executable
    return "python"
