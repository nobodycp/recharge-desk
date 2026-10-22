from __future__ import annotations

import time
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase

from providers import service
from providers.accounts import Credentials
from providers.adapters.sky.adapter import SkyAdapter
from providers.adapters.sky.client import SkySalesClient
from providers.contracts import (
    AuthRequired,
    BaseAdapter,
    ChargeContext,
    ChargeOutcome,
    ChargeResult,
    PermanentError,
    RawResponse,
    TransientError,
)
from providers.models import ProviderConnection
from providers.redis_conn import get_redis
from providers.registry import ADAPTERS
from providers.sanitize import scrub
from providers.sessions import SessionStore
from providers.tests.fakes import FakeChargeAdapter

TOTP_SECRET = "JBSWY3DPEHPK3PXP"
OLD_SESSION_FILE = Path("/tmp") / ".sky_sales_session.json"


def _ctx() -> ChargeContext:
    return ChargeContext(
        sale_id=1,
        phone="0599000000",
        expected_cost=Decimal("10"),
        product_name="x",
        is_esim=False,
        idempotency_key="k",
    )


class StubAdapter(BaseAdapter):
    key = "stub"

    def __init__(self, error: Exception):
        self.error = error
        self.calls = 0

    def _fail(self, *args):
        self.calls += 1
        raise self.error

    refresh = charge = check_charge = _fail


def _with_adapter(adapter):
    return patch("providers.service.get_adapter", return_value=adapter)


class ServiceTests(TestCase):
    def setUp(self):
        get_redis().flushall()
        ProviderConnection.objects.all().delete()

    def test_refresh_retries_transient_three_times_then_error_response(self):
        adapter = StubAdapter(TransientError("connection_error"))
        with _with_adapter(adapter), patch(
            "providers.service.time.sleep"
        ) as sleep, self.assertLogs("providers.service", level="WARNING") as logs:
            raw = service.refresh("stub", "0599000000")
        self.assertEqual(len(logs.output), 3)
        self.assertIsInstance(raw, RawResponse)
        self.assertEqual(raw.status_code, 0)
        self.assertEqual(raw.error, "connection_error")
        self.assertEqual(raw.text, "")
        self.assertIsNone(raw.json)
        self.assertEqual(adapter.calls, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [1, 2])

    def test_refresh_does_not_retry_login_path_failures(self):
        for code in ("login_busy", "login_failed"):
            adapter = StubAdapter(TransientError(code))
            with _with_adapter(adapter), patch(
                "providers.service.time.sleep"
            ) as sleep, self.assertLogs("providers.service", level="WARNING"):
                raw = service.refresh("stub", "0599000000")
            self.assertEqual(raw.error, code)
            self.assertEqual(adapter.calls, 1)
            sleep.assert_not_called()

    def test_refresh_stops_retrying_when_budget_would_be_exceeded(self):
        adapter = StubAdapter(TransientError("connection_error"))
        with _with_adapter(adapter), patch(
            "providers.service.time.sleep"
        ) as sleep, self.assertLogs("providers.service", level="WARNING"):
            raw = service.refresh("stub", "0599000000", budget=0.5)
        self.assertEqual(raw.error, "connection_error")
        self.assertEqual(adapter.calls, 1)
        sleep.assert_not_called()

        # 1.5 s fits the 1 s sleep but not the following 2 s one.
        adapter = StubAdapter(TransientError("connection_error"))
        with _with_adapter(adapter), patch(
            "providers.service.time.sleep"
        ) as sleep, self.assertLogs("providers.service", level="WARNING"):
            service.refresh("stub", "0599000000", budget=1.5)
        self.assertEqual(adapter.calls, 2)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [1])

    def test_refresh_with_lock_held_elsewhere_returns_within_budget_with_one_wait(self):
        from providers import sessions

        class NeedsLogin(BaseAdapter):
            key = "stub"
            needs_login = True
            calls = 0

            def refresh(self, phone):
                NeedsLogin.calls += 1
                raise AuthRequired("expired")

            def login(self):  # pragma: no cover - must not be reached
                raise AssertionError("login must not run while the lock is held")

        get_redis().set("provider:login-lock:stub", "other-worker", ex=30)
        real_acquire = sessions.NoLuaLock.acquire
        waits = []

        def counting_acquire(lock, *args, **kwargs):
            waits.append(lock.blocking_timeout)
            return real_acquire(lock, *args, **kwargs)

        started = time.monotonic()
        with _with_adapter(NeedsLogin()), patch.object(
            sessions.NoLuaLock, "acquire", counting_acquire
        ), self.assertLogs("providers.service", level="WARNING"):
            raw = service.refresh("stub", "0599000000", budget=1.0)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 2.0)
        self.assertEqual(raw.error, "login_busy")
        self.assertEqual(len(waits), 1)
        self.assertLessEqual(waits[0], 1.0)
        self.assertEqual(NeedsLogin.calls, 1)

    def _lock_wait_passed(self, budget):
        waits = []

        def fake_run(adapter, fn, lock_wait=None):
            waits.append(lock_wait)
            return RawResponse(text="ok", json=None, status_code=200)

        with _with_adapter(StubAdapter(RuntimeError("unused"))), patch(
            "providers.service.run_authenticated", side_effect=fake_run
        ):
            service.refresh("stub", "0599000000", budget=budget)
        return waits[0]

    def test_worker_budget_may_wait_past_the_web_lock_cap(self):
        from providers import sessions

        wait = self._lock_wait_passed(150)
        self.assertGreater(wait, sessions.LOGIN_LOCK_WAIT_SECONDS)
        self.assertLessEqual(wait, 150)
        self.assertLessEqual(wait, sessions.LOGIN_LOCK_TIMEOUT_SECONDS)

    def test_sync_budget_keeps_the_web_lock_cap(self):
        from providers import sessions

        self.assertLessEqual(self._lock_wait_passed(45), sessions.LOGIN_LOCK_WAIT_SECONDS)
        self.assertLessEqual(self._lock_wait_passed(45), 40)

    def test_charge_with_worker_budget_may_wait_past_the_web_lock_cap(self):
        from providers import sessions

        waits = []

        def fake_run(adapter, fn, lock_wait=None):
            waits.append(lock_wait)
            return ChargeOutcome(result=ChargeResult.SUCCESS)

        with _with_adapter(StubAdapter(RuntimeError("unused"))), patch(
            "providers.service.run_authenticated", side_effect=fake_run
        ):
            service.charge("stub", _ctx(), budget=150)
        self.assertGreater(waits[0], sessions.LOGIN_LOCK_WAIT_SECONDS)

    def test_refresh_permanent_error_is_error_response_without_retry(self):
        adapter = StubAdapter(PermanentError("config_error"))
        with _with_adapter(adapter), patch("providers.service.time.sleep") as sleep:
            raw = service.refresh("stub", "0599000000")
        self.assertEqual(raw.status_code, 0)
        self.assertEqual(raw.error, "config_error")
        self.assertEqual(adapter.calls, 1)
        sleep.assert_not_called()

    def test_refresh_unknown_provider_raises_keyerror(self):
        with self.assertRaises(KeyError):
            service.refresh("nope", "0599000000")

    def test_charge_permanent_error_is_failed(self):
        with _with_adapter(StubAdapter(PermanentError("rejected"))):
            outcome = service.charge("stub", _ctx())
        self.assertIsInstance(outcome, ChargeOutcome)
        self.assertEqual(outcome.result, ChargeResult.FAILED)

    def test_charge_transient_error_is_unverified(self):
        adapter = StubAdapter(TransientError("timeout"))
        with _with_adapter(adapter), self.assertLogs("providers.service", level="WARNING"):
            outcome = service.charge("stub", _ctx())
        self.assertEqual(outcome.result, ChargeResult.UNVERIFIED)
        self.assertEqual(adapter.calls, 1)

    def test_charge_unexpected_error_before_send_is_failed(self):
        with _with_adapter(StubAdapter(RuntimeError("boom"))), self.assertLogs(
            "providers.service", level="WARNING"
        ):
            outcome = service.charge("stub", _ctx())
        self.assertEqual(outcome.result, ChargeResult.FAILED)
        self.assertEqual(outcome.message, "فشل الشحن — خطأ في عملية الشحن")
        self.assertFalse(outcome.reached_provider)

    def test_charge_never_sent_because_login_lock_busy_is_failed(self):
        fake = FakeChargeAdapter.configured(
            needs_login=True, raise_before_send=AuthRequired("expired")
        )
        get_redis().set("provider:login-lock:fake", "other-worker", ex=30)
        with patch.dict(ADAPTERS, {"fake": fake}), self.assertLogs(
            "providers.service", level="WARNING"
        ):
            outcome = service.charge("fake", _ctx(), budget=0.2)
        self.assertEqual(outcome.result, ChargeResult.FAILED)
        self.assertEqual(outcome.message, "فشل الشحن — تسجيل الدخول قيد التنفيذ، حاول بعد قليل")
        self.assertEqual(fake.sent, 0)
        self.assertEqual(fake.logins, 0)

    def test_charge_never_sent_because_login_failed_is_failed(self):
        fake = FakeChargeAdapter.configured(
            needs_login=True,
            raise_before_send=AuthRequired("expired"),
            login_error=RuntimeError("bad otp"),
        )
        with patch.dict(ADAPTERS, {"fake": fake}), patch(
            "providers.sessions.accounts.record_login"
        ), self.assertLogs("providers.sessions", level="WARNING"), self.assertLogs(
            "providers.service", level="WARNING"
        ):
            outcome = service.charge("fake", _ctx())
        self.assertEqual(outcome.result, ChargeResult.FAILED)
        self.assertEqual(outcome.message, "فشل الشحن — خطأ تسجيل الدخول")
        self.assertEqual(fake.sent, 0)

    def test_charge_auth_required_after_fresh_login_is_failed_not_sent(self):
        fake = FakeChargeAdapter.configured(
            needs_login=True, raise_before_send=AuthRequired("expired")
        )
        with patch.dict(ADAPTERS, {"fake": fake}), patch(
            "providers.sessions.accounts.record_login"
        ), self.assertLogs("providers.service", level="WARNING"):
            outcome = service.charge("fake", _ctx())
        self.assertEqual(outcome.result, ChargeResult.FAILED)
        self.assertEqual(outcome.message, "فشل الشحن — خطأ تسجيل الدخول")
        self.assertEqual(fake.logins, 1)
        self.assertEqual(len(fake.charges), 2)
        self.assertEqual(fake.sent, 0)

    def test_charge_error_after_request_sent_stays_unverified(self):
        # Even a login-looking code is unverified once the adapter sent the request.
        for error in (TransientError("login_failed"), TransientError("timeout")):
            fake = FakeChargeAdapter.configured(raise_after_send=error)
            with patch.dict(ADAPTERS, {"fake": fake}), self.assertLogs(
                "providers.service", level="WARNING"
            ):
                outcome = service.charge("fake", _ctx())
            self.assertEqual(outcome.result, ChargeResult.UNVERIFIED)
            self.assertEqual(fake.sent, 1)

    def test_charge_never_retries_after_send(self):
        fake = FakeChargeAdapter.configured(raise_after_send=TransientError("timeout"))
        with patch.dict(ADAPTERS, {"fake": fake}), patch(
            "providers.service.time.sleep"
        ) as sleep, self.assertLogs("providers.service", level="WARNING"):
            service.charge("fake", _ctx())
        self.assertEqual(len(fake.charges), 1)
        sleep.assert_not_called()

    def test_check_charge_without_ref_needs_adapter_support(self):
        fake = FakeChargeAdapter.configured(check_without_ref=False)
        with patch.dict(ADAPTERS, {"fake": fake}):
            outcome = service.check_charge("fake", _ctx(), "")
        self.assertEqual(outcome.result, ChargeResult.UNVERIFIED)
        self.assertEqual(outcome.message, "no_provider_ref")
        self.assertEqual(fake.checks, [])

        fake = FakeChargeAdapter.configured(
            check_without_ref=True, check_result=ChargeResult.FAILED
        )
        with patch.dict(ADAPTERS, {"fake": fake}):
            outcome = service.check_charge("fake", _ctx(), "")
        self.assertEqual(outcome.result, ChargeResult.FAILED)
        self.assertEqual(len(fake.checks), 1)

    def test_check_charge_errors_are_unverified(self):
        with _with_adapter(StubAdapter(PermanentError("x"))), self.assertLogs(
            "providers.service", level="WARNING"
        ):
            outcome = service.check_charge("stub", _ctx(), "ref-1")
        self.assertEqual(outcome.result, ChargeResult.UNVERIFIED)

    def test_scrub_removes_password_and_jwt(self):
        text = scrub(
            '{"username": "u", "password": "s3cret", "jwtToken": "eyJabc.def"}'
            " | {'sessionId': 'SID-1', 'otpCode': '123456'}"
            " | user=u&password=p4ss&token=tok9&cookie=c00k"
        )
        for secret in ("s3cret", "eyJabc.def", "SID-1", "123456", "p4ss", "tok9", "c00k"):
            self.assertNotIn(secret, text)
        self.assertIn('"username": "u"', text)
        self.assertIn('"password": "***"', text)
        self.assertIn("user=u", text)
        self.assertIn("password=***", text)

    def test_scrub_masks_url_credentials(self):
        text = scrub(
            "ProxyError: HTTPSConnectionPool via http://LOGIN_s_sky:Pr0xyPass@res.geonix.com:10000 failed"
        )
        self.assertNotIn("Pr0xyPass", text)
        self.assertNotIn("LOGIN_s_sky", text)
        self.assertIn("http://***@res.geonix.com:10000", text)
        self.assertEqual(scrub("https://api.ipify.org/x"), "https://api.ipify.org/x")
        self.assertEqual(
            scrub("via http://u:p@ss@host:8080/x ok"), "via http://***@host:8080/x ok"
        )

    def test_record_login_masks_url_credentials(self):
        from providers.accounts import record_login
        from providers.crypto import encrypt
        from providers.models import ProviderConnection

        ProviderConnection.objects.create(
            provider_key="sky", username="u", secret_encrypted=encrypt("pw")
        )
        record_login("sky", False, "Failed via socks5://other:S3cret@1.2.3.4:1080")
        error = ProviderConnection.objects.get(provider_key="sky").last_error
        self.assertNotIn("S3cret", error)
        self.assertIn("socks5://***@1.2.3.4:1080", error)


def _fake_sky_post(responses: dict):
    def _post(self, path, payload):
        return responses[path]

    return _post


SKY_LOGIN_RESPONSES = {
    "/ipa/apis/json/general/login": {
        "result": "SUCCESS",
        "jwtToken": "jwt-1",
        "sessionId": "sid-1",
    },
    "/ipa/apis/json/factor/init2fa": {"result": "SUCCESS"},
    "/ipa/apis/json/factor/confirm2fa": {"result": "SUCCESS", "confirmed": True},
    "/ipa/apis/json/general/getLoginInfo": {
        "result": "SUCCESS",
        "userId": 1550,
        "userName": "user",
    },
    "/ipa/apis/json/general/genJwtToken": {"result": "SUCCESS", "jwtToken": "jwt-2"},
}


class SkySessionStoreTests(TestCase):
    def setUp(self):
        get_redis().flushall()
        creds = Credentials("user", "pass", TOTP_SECRET, {})
        patcher = patch(
            "providers.adapters.sky.adapter.get_credentials", return_value=creds
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_sky_refresh_without_session_raises_auth_required(self):
        with patch.object(SkySalesClient, "_post") as post:
            with self.assertRaises(AuthRequired):
                SkyAdapter().refresh("0599000000")
        post.assert_not_called()

    def test_sky_session_saved_in_store_not_tmp(self):
        before = OLD_SESSION_FILE.stat().st_mtime if OLD_SESSION_FILE.exists() else None
        with patch.object(SkySalesClient, "_post", _fake_sky_post(SKY_LOGIN_RESPONSES)):
            SkyAdapter().login()
        after = OLD_SESSION_FILE.stat().st_mtime if OLD_SESSION_FILE.exists() else None
        self.assertEqual(before, after)

        data = SessionStore("sky").load()
        self.assertEqual(data["jwt_token"], "jwt-2")
        self.assertEqual(data["eot_user_id"], 1550)
        self.assertFalse(data["pending_otp"])

    def test_otp_rejected_once_then_accepted_is_one_successful_login(self):
        confirms = iter(
            [
                {"result": "FAILED", "confirmed": False},
                {"result": "SUCCESS", "confirmed": True},
            ]
        )
        posts = []

        def _post(client_self, path, payload):
            posts.append(path)
            if path == "/ipa/apis/json/factor/confirm2fa":
                return next(confirms)
            return SKY_LOGIN_RESPONSES[path]

        result = {"success": True, "reason": "success", "phone": "0599000000", "message": "ok"}
        with patch.object(SkySalesClient, "_post", _post), patch.object(
            SkySalesClient, "refresh_sim", return_value=result
        ), patch("providers.sessions.accounts.record_login") as record_login:
            raw = service.refresh("sky", "0599000000")
        self.assertEqual(raw.status_code, 200)
        self.assertEqual(posts.count("/ipa/apis/json/general/login"), 2)
        self.assertEqual(posts.count("/ipa/apis/json/factor/confirm2fa"), 2)
        record_login.assert_called_once_with("sky", True)
        self.assertEqual(SessionStore("sky").generation(), 1)
        self.assertFalse(get_redis().exists("provider:login-cooldown:sky"))
        self.assertEqual(SessionStore("sky").load()["jwt_token"], "jwt-2")

    def test_service_refresh_logs_in_then_refreshes(self):
        result = {"success": True, "reason": "success", "phone": "0599000000", "message": "ok"}
        with patch.object(
            SkySalesClient, "_post", _fake_sky_post(SKY_LOGIN_RESPONSES)
        ), patch.object(SkySalesClient, "refresh_sim", return_value=result) as refresh_sim:
            raw = service.refresh("sky", "0599000000")
        self.assertIsNone(raw.error)
        self.assertEqual(raw.status_code, 200)
        self.assertEqual(raw.json["reason"], "success")
        refresh_sim.assert_called_once_with("0599000000")
        self.assertEqual(SessionStore("sky").generation(), 1)


class KeepaliveTaskTests(TestCase):
    def setUp(self):
        get_redis().flushall()
        ProviderConnection.objects.all().delete()

    def test_keepalive_pings_login_providers_only_and_never_raises(self):
        from providers.tasks import keepalive_sessions

        creds = Credentials("user", "pass", "", {})
        with patch("providers.tasks.get_credentials", return_value=creds), patch(
            "providers.tasks.service.keepalive"
        ) as keepalive:
            keepalive_sessions()
        self.assertEqual(
            [call.args[0] for call in keepalive.call_args_list],
            ["sky", "layan", "areen"],
        )

        with patch("providers.tasks.get_credentials", return_value=creds), patch(
            "providers.tasks.service.keepalive", side_effect=TransientError("login_failed")
        ), self.assertLogs("providers.tasks", level="WARNING") as logs:
            keepalive_sessions()
        self.assertIn("login_failed", logs.output[0])

    def test_keepalive_covers_env_only_credentials(self):
        from providers.models import ProviderConnection
        from providers.tasks import keepalive_sessions

        self.assertFalse(ProviderConnection.objects.exists())
        env = {"SKY_SALES_USER": "user", "SKY_SALES_PASSWORD": "pass"}
        with patch.dict("os.environ", env), patch(
            "providers.tasks.service.keepalive"
        ) as keepalive:
            keepalive_sessions()
        keepalive.assert_called_once_with("sky")

    def test_keepalive_skips_missing_credentials_and_disabled_accounts(self):
        from providers.models import ProviderConnection
        from providers.tasks import keepalive_sessions

        with patch(
            "providers.tasks.get_credentials", side_effect=PermanentError("config_error")
        ), patch("providers.tasks.service.keepalive") as keepalive:
            keepalive_sessions()
        keepalive.assert_not_called()

        ProviderConnection.objects.create(provider_key="sky", is_enabled=False)
        ProviderConnection.objects.create(provider_key="layan", is_enabled=False)
        ProviderConnection.objects.create(provider_key="areen", is_enabled=False)
        with patch(
            "providers.tasks.get_credentials", return_value=Credentials("u", "p")
        ), patch("providers.tasks.service.keepalive") as keepalive:
            keepalive_sessions()
        keepalive.assert_not_called()

    def test_service_keepalive_runs_ping_with_login(self):
        class PingAdapter(BaseAdapter):
            key = "stub"
            needs_login = True
            logins = 0

            def login(self):
                PingAdapter.logins += 1

            def ping(self):
                if PingAdapter.logins == 0:
                    raise AuthRequired("expired")

        with _with_adapter(PingAdapter()):
            service.keepalive("stub")
        self.assertEqual(PingAdapter.logins, 1)

    def test_beat_schedule_runs_keepalive_every_five_minutes(self):
        from django.conf import settings

        entry = settings.CELERY_BEAT_SCHEDULE["providers-keepalive"]
        self.assertEqual(entry["task"], "providers.tasks.keepalive_sessions")
        self.assertEqual(entry["schedule"], 300.0)


class HealthTests(TestCase):
    def setUp(self):
        get_redis().flushall()
        ProviderConnection.objects.all().delete()

    def test_status_reports_redis_worker_and_accounts(self):
        from django.utils import timezone

        from providers import health
        from providers.models import ProviderConnection

        now = timezone.now()
        ProviderConnection.objects.create(
            provider_key="sky", last_login_at=now, last_login_ok=True
        )
        data = health.status()
        self.assertIs(data["redis_ok"], True)
        self.assertIs(data["worker_ok"], True)
        self.assertEqual(
            data["accounts"],
            [{"key": "sky", "last_login_at": now, "last_login_ok": True}],
        )

        with patch.object(type(get_redis()), "ping", side_effect=ConnectionError("x")):
            with self.assertLogs("providers.health", level="WARNING"):
                self.assertIs(health.status()["redis_ok"], False)

    def test_status_does_not_ping_worker_when_redis_is_down(self):
        from providers import health

        with patch.object(health, "_redis_ok", return_value=False), patch.object(
            health, "_worker_ok", return_value=True
        ) as worker_ok:
            data = health.status()
        worker_ok.assert_not_called()
        self.assertIs(data["redis_ok"], False)
        self.assertIs(data["worker_ok"], False)

    def test_worker_ping_waits_for_a_single_reply(self):
        from django.test import override_settings

        from providers import health

        with override_settings(CELERY_TASK_ALWAYS_EAGER=False), patch(
            "celery.app.control.Control.ping", return_value=[{"w": {"ok": "pong"}}]
        ) as ping:
            self.assertIs(health._worker_ok(), True)
        ping.assert_called_once_with(timeout=1, limit=1)
