from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from phone_refresh.providers.sky import SkyProvider


class SkyProviderCaptchaTests(SimpleTestCase):
    @patch("phone_refresh.providers.sky.solve_sky_recaptcha_v3")
    def test_default_backend_uses_firefox(self, solve_mock):
        solve_mock.return_value = "firefox-token"
        provider = SkyProvider()

        token = provider._fetch_captcha_token()

        self.assertEqual(token, "firefox-token")
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
