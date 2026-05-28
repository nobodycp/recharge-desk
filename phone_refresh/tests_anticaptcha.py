"""Tests for anti-captcha.com reCAPTCHA v3 client."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from phone_refresh.providers.captcha.anticaptcha import (
    AntiCaptchaError,
    solve_recaptcha_v3_anticaptcha,
)


class AntiCaptchaErrorHandlingTests(SimpleTestCase):
    @patch("phone_refresh.providers.captcha.anticaptcha.requests.post")
    def test_create_task_api_error(self, post_mock):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {
            "errorId": 1,
            "errorCode": "ERROR_KEY_DOES_NOT_EXIST",
            "errorDescription": "bad key",
        }
        post_mock.return_value = resp

        with self.assertRaises(AntiCaptchaError) as ctx:
            solve_recaptcha_v3_anticaptcha(api_key="bad-key")
        self.assertIn("ERROR_KEY_DOES_NOT_EXIST", str(ctx.exception))
