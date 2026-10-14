from __future__ import annotations

from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from providers import login_test, tasks
from providers.accounts import record_login
from providers.contracts import PermanentError, TransientError
from providers.forms import ProviderAccountForm
from providers.models import ProviderConnection
from providers.redis_conn import get_redis
from providers.sessions import (
    SessionStore,
    _cooldown_key,
    _failures_key,
    reset_session,
)

KEY = "sky"
CACHE_KEY = f"providers:login-test:{KEY}"


def _make_account(**form_kw) -> ProviderConnection:
    data = {"username": "u", "password": "pw"}
    data.update(form_kw)
    form = ProviderAccountForm(data, provider_key=KEY)
    assert form.is_valid(), form.errors
    return form.save()


class _Base(TestCase):
    def setUp(self):
        ProviderConnection.objects.all().delete()
        get_redis().flushall()
        cache.delete(CACHE_KEY)
        self.addCleanup(cache.delete, CACHE_KEY)


class ResetSessionTests(_Base):
    def test_reset_session_clears_session_and_cooldown_keys(self):
        redis_client = get_redis()
        SessionStore(KEY).save({"jwt_token": "x"})
        redis_client.set(_cooldown_key(KEY), "1")
        redis_client.set(_failures_key(KEY), "3")
        reset_session(KEY)
        self.assertIsNone(SessionStore(KEY).load())
        self.assertFalse(redis_client.exists(_cooldown_key(KEY)))
        self.assertFalse(redis_client.exists(_failures_key(KEY)))


class StartStatusTests(_Base):
    def test_start_enqueues_once(self):
        _make_account()
        with patch.object(tasks.test_connection, "delay") as delay:
            self.assertEqual(login_test.start(KEY), "started")
            self.assertEqual(login_test.start(KEY), "pending")
        delay.assert_called_once_with(KEY)

    def test_start_unavailable_when_enqueue_fails(self):
        _make_account()
        with patch.object(tasks.test_connection, "delay", side_effect=OSError("down")):
            self.assertEqual(login_test.start(KEY), "unavailable")
        self.assertIsNone(cache.get(CACHE_KEY))

    def test_start_again_after_login_recorded(self):
        _make_account()
        with patch.object(tasks.test_connection, "delay") as delay:
            self.assertEqual(login_test.start(KEY), "started")
            record_login(KEY, True)
            self.assertEqual(login_test.start(KEY), "started")
        self.assertEqual(delay.call_count, 2)

    def test_status_pending_until_login_recorded(self):
        _make_account()
        self.assertFalse(login_test.status(KEY)["pending"])
        with patch.object(tasks.test_connection, "delay"):
            login_test.start(KEY)
        status = login_test.status(KEY)
        self.assertTrue(status["pending"])
        record_login(KEY, True)
        status = login_test.status(KEY)
        self.assertFalse(status["pending"])
        self.assertTrue(status["last_login_ok"])
        self.assertIsNotNone(status["last_login_at"])
        self.assertEqual(status["last_error"], "")

    def test_status_without_account(self):
        status = login_test.status(KEY)
        self.assertEqual(
            status,
            {
                "pending": False,
                "last_login_at": None,
                "last_login_ok": None,
                "last_error": "",
            },
        )


class TestConnectionTaskTests(_Base):
    def test_task_resets_then_keepalive(self):
        _make_account()
        SessionStore(KEY).save({"jwt_token": "stale"})
        get_redis().set(_cooldown_key(KEY), "1")
        seen = {}

        def fake_keepalive(key):
            seen["session"] = SessionStore(key).load()
            seen["cooldown"] = get_redis().exists(_cooldown_key(key))

        with patch("providers.tasks.service.keepalive", side_effect=fake_keepalive) as ka:
            tasks.test_connection(KEY)
        ka.assert_called_once_with(KEY)
        self.assertIsNone(seen["session"])
        self.assertFalse(seen["cooldown"])

    def test_task_is_routed_to_refresh_queue(self):
        from django.conf import settings

        self.assertEqual(
            settings.CELERY_TASK_ROUTES["providers.tasks.*"], {"queue": "refresh"}
        )
        self.assertEqual(tasks.test_connection.name, "providers.tasks.test_connection")

    def test_config_error_is_recorded_for_the_ui(self):
        # No credentials at all: keepalive raises before any login attempt.
        account = ProviderConnection.objects.create(provider_key=KEY, username="")
        before = timezone.now()
        with patch.dict("os.environ", {"SKY_SALES_USER": "", "SKY_SALES_PASSWORD": ""}):
            tasks.test_connection(KEY)  # must not raise
        account.refresh_from_db()
        self.assertIsNotNone(account.last_login_at)
        self.assertGreaterEqual(account.last_login_at, before)
        self.assertFalse(account.last_login_ok)
        self.assertIn("config_error", account.last_error)

    def test_transient_error_without_login_attempt_is_recorded(self):
        account = _make_account()
        with patch(
            "providers.tasks.service.keepalive", side_effect=TransientError("login_busy")
        ):
            tasks.test_connection(KEY)
        account.refresh_from_db()
        self.assertFalse(account.last_login_ok)
        self.assertIn("login_busy", account.last_error)

    def test_unexpected_error_is_swallowed_and_recorded(self):
        account = _make_account()
        with patch("providers.tasks.service.keepalive", side_effect=RuntimeError("redis down")):
            tasks.test_connection(KEY)
        account.refresh_from_db()
        self.assertFalse(account.last_login_ok)
        self.assertIsNotNone(account.last_login_at)

    def test_existing_login_result_not_overwritten(self):
        account = _make_account()

        def keepalive(key):
            # Mimics _login_once: records its own failure, then raises.
            record_login(key, False, "bad password")
            raise TransientError("login_failed")

        with patch("providers.tasks.service.keepalive", side_effect=keepalive):
            tasks.test_connection(KEY)
        account.refresh_from_db()
        self.assertEqual(account.last_error, "bad password")

    def test_success_leaves_recorded_login_alone(self):
        account = _make_account()

        def keepalive(key):
            record_login(key, True)

        with patch("providers.tasks.service.keepalive", side_effect=keepalive):
            tasks.test_connection(KEY)
        account.refresh_from_db()
        self.assertTrue(account.last_login_ok)

    def test_permanent_error_does_not_raise(self):
        _make_account()
        with patch(
            "providers.tasks.service.keepalive", side_effect=PermanentError("provider_disabled")
        ):
            tasks.test_connection(KEY)


class FormTests(_Base):
    def test_form_has_no_is_enabled_field(self):
        form = ProviderAccountForm(provider_key=KEY)
        self.assertNotIn("is_enabled", form.fields)

    def test_form_save_resets_session(self):
        _make_account()
        SessionStore(KEY).save({"jwt_token": "stale"})
        get_redis().set(_cooldown_key(KEY), "1")
        get_redis().set(_failures_key(KEY), "2")
        form = ProviderAccountForm({"username": "u2", "password": ""}, provider_key=KEY)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertIsNone(SessionStore(KEY).load())
        self.assertFalse(get_redis().exists(_cooldown_key(KEY)))
        self.assertFalse(get_redis().exists(_failures_key(KEY)))

    def test_form_save_keeps_is_enabled(self):
        account = _make_account()
        self.assertTrue(account.is_enabled)  # model default for a new row
        ProviderConnection.objects.filter(pk=account.pk).update(is_enabled=False)
        form = ProviderAccountForm({"username": "u", "password": ""}, provider_key=KEY)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        account.refresh_from_db()
        self.assertFalse(account.is_enabled)


class SkyPingAfterResetTests(_Base):
    def test_ping_with_empty_session_requires_auth(self):
        from providers.contracts import AuthRequired
        from providers.registry import get_adapter

        _make_account()
        reset_session(KEY)
        with self.assertRaises(AuthRequired):
            get_adapter(KEY).ping()
