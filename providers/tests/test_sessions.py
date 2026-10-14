from __future__ import annotations

import threading
import time
from unittest.mock import patch

from django.db import connection
from django.test import TestCase

from providers.contracts import AuthRequired, BaseAdapter, TransientError
from providers.crypto import encrypt
from providers.models import ProviderAccount
from providers.redis_conn import get_redis
from providers.sessions import SessionStore, run_authenticated


class FakeAdapter(BaseAdapter):
    key = "sky"
    needs_login = True

    def __init__(self, login_delay: float = 0.0, login_error: Exception | None = None):
        self.login_calls = 0
        self.login_delay = login_delay
        self.login_error = login_error
        self._calls_lock = threading.Lock()

    def login(self) -> None:
        with self._calls_lock:
            self.login_calls += 1
        if self.login_delay:
            time.sleep(self.login_delay)
        if self.login_error is not None:
            raise self.login_error


class SessionStoreTests(TestCase):
    def setUp(self):
        get_redis().flushall()

    def test_save_load_clear_and_generation(self):
        store = SessionStore("sky")
        self.assertIsNone(store.load())
        store.save({"jwt_token": "abc"})
        self.assertEqual(store.load(), {"jwt_token": "abc"})
        ttl = get_redis().ttl("provider:session:sky")
        self.assertGreater(ttl, 12 * 3600 - 10)
        self.assertLessEqual(ttl, 12 * 3600)
        store.clear()
        self.assertIsNone(store.load())

        self.assertEqual(store.generation(), 0)
        self.assertEqual(store.bump_generation(), 1)
        self.assertEqual(store.generation(), 1)
        self.assertEqual(int(get_redis().get("provider:session-gen:sky")), 1)


class RunAuthenticatedTests(TestCase):
    def setUp(self):
        get_redis().flushall()

    def test_auth_required_triggers_single_login_and_retry(self):
        adapter = FakeAdapter()
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            if calls["n"] == 1:
                raise AuthRequired("expired")
            return "ok"

        self.assertEqual(run_authenticated(adapter, fn), "ok")
        self.assertEqual(adapter.login_calls, 1)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(SessionStore("sky").generation(), 1)
        self.assertIsNone(get_redis().get("provider:login-lock:sky"))

    def test_no_login_when_fn_succeeds(self):
        adapter = FakeAdapter()
        self.assertEqual(run_authenticated(adapter, lambda: "ok"), "ok")
        self.assertEqual(adapter.login_calls, 0)

    def test_concurrent_auth_required_logs_in_once(self):
        adapter = FakeAdapter(login_delay=0.2)
        store = SessionStore("sky")
        start = store.generation()

        def fn():
            if store.generation() <= start:
                raise AuthRequired("expired")
            return "ok"

        results: list = []
        errors: list = []
        barrier = threading.Barrier(5)

        def worker():
            try:
                barrier.wait(timeout=5)
                results.append(run_authenticated(adapter, fn))
            except Exception as exc:  # pragma: no cover - surfaced by asserts
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(errors, [])
        self.assertEqual(adapter.login_calls, 1)
        self.assertEqual(results, ["ok"] * 5)

    def test_second_auth_required_propagates(self):
        adapter = FakeAdapter()
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise AuthRequired("expired")

        with self.assertRaises(AuthRequired):
            run_authenticated(adapter, fn)
        self.assertEqual(adapter.login_calls, 1)
        self.assertEqual(calls["n"], 2)

    def test_login_failure_becomes_transient_and_records_account_error(self):
        ProviderAccount.objects.create(
            provider_key="sky",
            username="user",
            secret_encrypted=encrypt("hunter2"),
        )
        adapter = FakeAdapter(
            login_error=Exception(
                "Login failed: {'password': 'hunter2', 'jwtToken': 'eyJsecret'}"
            )
        )

        def fn():
            raise AuthRequired("expired")

        with self.assertRaises(TransientError) as ctx, self.assertLogs(
            "providers.sessions", level="WARNING"
        ) as logs:
            run_authenticated(adapter, fn)
        self.assertEqual(str(ctx.exception), "login_failed")
        self.assertNotIn("hunter2", logs.output[0])
        self.assertEqual(SessionStore("sky").generation(), 0)

        account = ProviderAccount.objects.get(provider_key="sky")
        self.assertIs(account.last_login_ok, False)
        self.assertIsNotNone(account.last_login_at)
        self.assertIn("Login failed", account.last_error)
        self.assertNotIn("hunter2", account.last_error)
        self.assertNotIn("eyJsecret", account.last_error)

    def test_login_success_records_account(self):
        ProviderAccount.objects.create(
            provider_key="sky",
            username="user",
            secret_encrypted=encrypt("hunter2"),
            last_login_ok=False,
            last_error="old",
        )
        adapter = FakeAdapter()
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            if calls["n"] == 1:
                raise AuthRequired("expired")
            return "ok"

        run_authenticated(adapter, fn)
        account = ProviderAccount.objects.get(provider_key="sky")
        self.assertIs(account.last_login_ok, True)
        self.assertEqual(account.last_error, "")

    def test_lock_timeout_is_transient(self):
        adapter = FakeAdapter()
        get_redis().set("provider:login-lock:sky", "other-worker", ex=30)

        def fn():
            raise AuthRequired("expired")

        with patch("providers.sessions.LOGIN_LOCK_WAIT_SECONDS", 0.2):
            with self.assertRaises(TransientError) as ctx:
                run_authenticated(adapter, fn)
        self.assertEqual(str(ctx.exception), "login_lock_timeout")
        self.assertEqual(adapter.login_calls, 0)
