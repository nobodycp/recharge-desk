import json
import time
from datetime import date
from unittest.mock import patch

import requests
from django.test import SimpleTestCase

from providers.accounts import Credentials
from providers.adapters.sky.adapter import SkyAdapter, _SKY_SYSTEM_ERROR_REASONS
from providers.adapters.sky.client import (
    SkySalesClient,
    SkySalesError,
    SkySalesSessionError,
    proxy_fingerprint,
)
from providers.contracts import AuthRequired, PermanentError, TransientError
from providers.redis_conn import get_redis
from providers.sessions import SessionStore

REFRESH_SIM = "providers.adapters.sky.client.SkySalesClient.refresh_sim"


def _ready_session(**overrides):
    data = {
        "jwt_token": "jwt",
        "session_id": "sid",
        "eot_user_id": 1550,
        "user_name": "user",
        "pending_otp": False,
        "cookies": {},
        "bound_ip": "",
        "proxy_fingerprint": proxy_fingerprint(""),
        "logged_in_at": time.time(),
    }
    data.update(overrides)
    return data


class SkyAdapterTests(SimpleTestCase):
    def setUp(self):
        get_redis().flushall()
        SessionStore("sky").save(_ready_session())
        patcher = patch(
            "providers.adapters.sky.adapter.get_credentials",
            return_value=Credentials("user", "pass", "", {}),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_needs_login(self):
        self.assertTrue(SkyAdapter.needs_login)

    @patch(REFRESH_SIM)
    def test_success_returns_json_without_error(self, refresh_sim):
        refresh_sim.return_value = {
            "success": True,
            "message": "تم التحديث بنجاح - رقم العملية: 4520994",
            "phone": "0523971893",
            "reason": "success",
            "task_id": "4520994",
        }
        raw = SkyAdapter().refresh("0523971893")
        self.assertIsNone(raw.error)
        self.assertEqual(raw.status_code, 200)
        self.assertEqual(raw.json["reason"], "success")

    @patch(REFRESH_SIM)
    def test_business_failures_are_422_responses(self, refresh_sim):
        for reason in ("not_found", "not_active", "not_eligible"):
            refresh_sim.return_value = {
                "success": False,
                "message": "لم يتم التحديث",
                "phone": "0555544011",
                "reason": reason,
            }
            raw = SkyAdapter().refresh("0555544011")
            self.assertIsNone(raw.error)
            self.assertEqual(raw.status_code, 422)
            self.assertEqual(raw.json["reason"], reason)

    @patch(REFRESH_SIM)
    def test_system_error_sets_raw_error_and_scrubs_secrets(self, refresh_sim):
        refresh_sim.return_value = {
            "success": False,
            "message": "خطأ أثناء التحديث",
            "phone": "0555544071",
            "reason": "api_error",
            "error": "GetSubscriberCosts failed: {'result': 'X', 'jwtToken': 'eyJsecret'}",
        }
        raw = SkyAdapter().refresh("0555544071")
        self.assertEqual(raw.status_code, 0)
        self.assertIn("GetSubscriberCosts failed", raw.error)
        self.assertNotIn("eyJsecret", raw.error)
        self.assertNotIn("eyJsecret", raw.text)

    def test_system_error_reasons_complete(self):
        self.assertIn("proxy_error", _SKY_SYSTEM_ERROR_REASONS)
        self.assertIn("login_error", _SKY_SYSTEM_ERROR_REASONS)

    @patch(REFRESH_SIM)
    def test_response_text_is_json(self, refresh_sim):
        payload = {"success": True, "reason": "success", "phone": "0555544071", "message": "ok"}
        refresh_sim.return_value = payload
        raw = SkyAdapter().refresh("0555544071")
        self.assertEqual(json.loads(raw.text), payload)

    @patch(REFRESH_SIM, side_effect=SkySalesSessionError("REJECTED on /x: {}"))
    def test_session_error_raises_auth_required(self, _refresh_sim):
        with self.assertRaises(AuthRequired):
            SkyAdapter().refresh("0555544071")

    @patch(REFRESH_SIM, side_effect=requests.ConnectionError("down"))
    def test_connection_error_raises_transient(self, _refresh_sim):
        with self.assertRaises(TransientError):
            SkyAdapter().refresh("0555544071")

    def test_pending_otp_session_is_cleared_and_requires_login(self):
        SessionStore("sky").save(_ready_session(pending_otp=True))
        with self.assertRaises(AuthRequired):
            SkyAdapter().refresh("0555544071")
        self.assertIsNone(SessionStore("sky").load())

    def test_proxy_change_requires_login(self):
        SessionStore("sky").save(_ready_session(proxy_fingerprint="other"))
        with self.assertRaises(AuthRequired):
            SkyAdapter().refresh("0555544071")

    def test_fetch_balance_rows_uses_stored_session(self):
        rows = [{"ext_return_value_6": "0528819116"}]
        with patch.object(
            SkySalesClient, "fetch_balance_report", return_value=rows
        ) as fetch:
            out = SkyAdapter().fetch_balance_rows(date(2026, 8, 1), date(2026, 8, 31))
        self.assertEqual(out, rows)
        fetch.assert_called_once_with(date(2026, 8, 1), date(2026, 8, 31))

    def test_fetch_balance_rows_maps_errors(self):
        with patch.object(
            SkySalesClient,
            "fetch_balance_report",
            side_effect=SkySalesSessionError("FAILED on /generic/v2"),
        ):
            with self.assertRaises(AuthRequired):
                SkyAdapter().fetch_balance_rows(date(2026, 8, 1), date(2026, 8, 31))
        with patch.object(
            SkySalesClient,
            "fetch_balance_report",
            side_effect=SkySalesError("GetEot4RReportData failed: ERROR"),
        ):
            with self.assertRaises(PermanentError):
                SkyAdapter().fetch_balance_rows(date(2026, 8, 1), date(2026, 8, 31))

    def test_ping_probes_session(self):
        with patch.object(SkySalesClient, "_probe_session") as probe:
            SkyAdapter().ping()
        probe.assert_called_once()
        with patch.object(
            SkySalesClient, "_probe_session", side_effect=SkySalesSessionError("x")
        ):
            with self.assertRaises(AuthRequired):
                SkyAdapter().ping()
