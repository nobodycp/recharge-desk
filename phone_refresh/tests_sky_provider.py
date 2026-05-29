import json
from unittest.mock import patch

from django.test import SimpleTestCase

from phone_refresh.providers.sky import SkyProvider, _SKY_SYSTEM_ERROR_REASONS


class SkyProviderTests(SimpleTestCase):
    @patch("phone_refresh.providers.sky.execute_refresh")
    def test_success_returns_json_without_error(self, execute_mock):
        execute_mock.return_value = {
            "success": True,
            "message": "تم التحديث بنجاح - رقم العملية: 4520994",
            "phone": "0523971893",
            "reason": "success",
            "task_id": "4520994",
        }
        raw = SkyProvider().call("0523971893")
        self.assertIsNone(raw.error)
        self.assertEqual(raw.status_code, 200)
        self.assertEqual(raw.json["reason"], "success")

    @patch("phone_refresh.providers.sky.execute_refresh")
    def test_not_found_is_business_response(self, execute_mock):
        execute_mock.return_value = {
            "success": False,
            "message": "لم يتم التحديث",
            "phone": "0555544011",
            "reason": "not_found",
        }
        raw = SkyProvider().call("0555544011")
        self.assertIsNone(raw.error)
        self.assertEqual(raw.status_code, 422)
        self.assertIn("not_found", raw.text)

    @patch("phone_refresh.providers.sky.execute_refresh")
    def test_proxy_error_sets_raw_error(self, execute_mock):
        execute_mock.return_value = {
            "success": False,
            "message": "فشل الاتصال — تحقق من البروكسي",
            "phone": "0555544071",
            "reason": "proxy_error",
            "error": "Failed to detect egress IP: timeout",
        }
        raw = SkyProvider().call("0555544071")
        self.assertEqual(raw.status_code, 0)
        self.assertIn("timeout", raw.error or "")

    def test_system_error_reasons_complete(self):
        self.assertIn("proxy_error", _SKY_SYSTEM_ERROR_REASONS)
        self.assertIn("login_error", _SKY_SYSTEM_ERROR_REASONS)

    @patch("phone_refresh.providers.sky.execute_refresh")
    def test_response_text_is_json(self, execute_mock):
        payload = {"success": True, "reason": "success", "phone": "0555544071", "message": "ok"}
        execute_mock.return_value = payload
        raw = SkyProvider().call("0555544071")
        self.assertEqual(json.loads(raw.text), payload)
