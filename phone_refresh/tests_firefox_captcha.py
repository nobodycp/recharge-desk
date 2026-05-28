import os
from unittest.mock import patch

from django.test import SimpleTestCase

from phone_refresh.providers.captcha.firefox_browser import (
    FirefoxCaptchaError,
    _playwright_proxy,
)


class PlaywrightProxyEnvTests(SimpleTestCase):
    def test_empty_proxy_returns_none(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SKY_PLAYWRIGHT_PROXY", None)
            self.assertIsNone(_playwright_proxy())

    def test_parses_user_password_host_port(self):
        with patch.dict(
            os.environ,
            {"SKY_PLAYWRIGHT_PROXY": "http://user:secret@proxy.example:8080"},
        ):
            self.assertEqual(
                _playwright_proxy(),
                {
                    "server": "http://proxy.example:8080",
                    "username": "user",
                    "password": "secret",
                },
            )

    def test_rejects_invalid_proxy(self):
        with patch.dict(os.environ, {"SKY_PLAYWRIGHT_PROXY": "not-a-url"}):
            with self.assertRaises(FirefoxCaptchaError):
                _playwright_proxy()
