from __future__ import annotations

from pathlib import Path
from unittest import TestCase
import os
import tempfile

from booking_cli.config import load_settings


class ConfigTests(TestCase):
    def test_config_and_env_precedence(self) -> None:
        config_text = """
base_url = "https://config.example"
aid = 111111

[defaults]
language = "de-de"
currency = "GBP"
adults = 2
rooms = 2
timeout = 15
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "booking-cli.toml"
            config_path.write_text(config_text, encoding="utf-8")
            session_cache_path = Path(temp_dir) / "session.json"

            old_env = os.environ.copy()
            os.environ["BOOKING_CLI_CURRENCY"] = "USD"
            os.environ["BOOKING_CLI_TIMEOUT"] = "30"
            os.environ["BOOKING_CLI_SESSION_CACHE"] = str(session_cache_path)
            os.environ["BOOKING_CLI_SESSION_TTL_MINUTES"] = "15"
            os.environ["BOOKING_CLI_BROWSER_HEADLESS"] = "true"
            try:
                settings = load_settings(config_path)
            finally:
                os.environ.clear()
                os.environ.update(old_env)

        self.assertEqual(settings.base_url, "https://config.example")
        self.assertEqual(settings.aid, 111111)
        self.assertEqual(settings.language, "de-de")
        self.assertEqual(settings.currency, "USD")
        self.assertEqual(settings.adults, 2)
        self.assertEqual(settings.rooms, 2)
        self.assertEqual(settings.timeout, 30.0)
        self.assertEqual(settings.session_cache_path, session_cache_path)
        self.assertEqual(settings.session_ttl_minutes, 15)
        self.assertTrue(settings.browser_headless)
