import os
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from phone_refresh.providers.captcha.anticaptcha import (
    AntiCaptchaError,
    _normalized_min_score,
    solve_recaptcha_v3_anticaptcha,
)
from phone_refresh.providers.sky import SkyProvider


class AntiCaptchaMinScoreTests(SimpleTestCase):
    def test_snaps_to_allowed_values(self):
        self.assertEqual(_normalized_min_score("0.9"), 0.9)
        self.assertEqual(_normalized_min_score("0.25"), 0.3)
        self.assertEqual(_normalized_min_score("0.75"), 0.7)


class AntiCaptchaApiKeyTests(SimpleTestCase):
    def test_missing_api_key_raises(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANTICAPTCHA_API_KEY", None)
            with self.assertRaises(AntiCaptchaError):
                solve_recaptcha_v3_anticaptcha()


class AntiCaptchaSolveTests(SimpleTestCase):
    @patch("phone_refresh.providers.captcha.anticaptcha.time.sleep")
    @patch("phone_refresh.providers.captcha.anticaptcha.requests.post")
    def test_create_and_poll_returns_token(self, post_mock, _sleep_mock):
        create_resp = MagicMock()
        create_resp.raise_for_status.return_value = None
        create_resp.json.return_value = {"errorId": 0, "taskId": 99}

        ready_resp = MagicMock()
        ready_resp.raise_for_status.return_value = None
        ready_resp.json.return_value = {
            "errorId": 0,
            "status": "ready",
            "solution": {"gRecaptchaResponse": "anticaptcha-token"},
        }

        post_mock.side_effect = [create_resp, ready_resp]

        token = solve_recaptcha_v3_anticaptcha(api_key="test-key", timeout_sec=30)
        self.assertEqual(token, "anticaptcha-token")
        self.assertEqual(post_mock.call_count, 2)


class SkyProviderCaptchaTests(SimpleTestCase):
    @patch("phone_refresh.providers.sky.solve_recaptcha_v3_anticaptcha")
    def test_default_backend_uses_anticaptcha(self, solve_mock):
        solve_mock.return_value = "anticaptcha-token"
        provider = SkyProvider()

        token = provider._fetch_captcha_token()

        self.assertEqual(token, "anticaptcha-token")
        solve_mock.assert_called_once_with()

    @patch.dict("os.environ", {"SKY_CAPTCHA_BACKEND": "bypass"})
    @patch("phone_refresh.providers.sky.RecaptchaV3Bypass")
    def test_bypass_backend_when_configured(self, bypass_cls):
        bypass_cls.return_value.response.return_value = "bypass-token"
        provider = SkyProvider()

        token = provider._fetch_captcha_token()

        self.assertEqual(token, "bypass-token")
        bypass_cls.assert_called_once()


class SkyProviderPollTests(SimpleTestCase):
    @patch("phone_refresh.providers.sky.requests.get")
    def test_poll_stops_on_terminal_failure(self, get_mock):
        get_mock.return_value = MagicMock(
            json=lambda: {
                "success": False,
                "message": "لقد قمت بتحديث الرقم قبل قليل الرجاء الانتظار",
            }
        )
        provider = SkyProvider()

        result = provider._poll_status("test-id")

        self.assertFalse(result["success"])
        get_mock.assert_called_once()
