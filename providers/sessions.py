"""Provider sessions in Redis plus the single-login-per-expiry guard."""
from __future__ import annotations

import json
import logging
from typing import Callable, TypeVar

from redis.exceptions import LockError, LockNotOwnedError, WatchError
from redis.lock import Lock

from providers import accounts
from providers.contracts import AuthRequired, BaseAdapter, TransientError
from providers.redis_conn import get_redis
from providers.sanitize import scrub

log = logging.getLogger(__name__)

T = TypeVar("T")

SESSION_TTL_SECONDS = 12 * 3600
# Invariant: an adapter's worst-case login duration < LOGIN_LOCK_TIMEOUT_SECONDS.
LOGIN_LOCK_TIMEOUT_SECONDS = 180
# Default lock wait for in-request callers (must stay below gunicorn's timeout).
LOGIN_LOCK_WAIT_SECONDS = 40
# Cool-down after a failed login doubles per consecutive failure, up to the cap;
# the failure counter is forgotten after LOGIN_FAILURES_TTL_SECONDS or a success.
LOGIN_COOLDOWN_SECONDS = 60
LOGIN_COOLDOWN_MAX_SECONDS = 15 * 60
LOGIN_FAILURES_TTL_SECONDS = 3600


def _as_bytes(value) -> bytes | None:
    return value.encode() if isinstance(value, str) else value


class NoLuaLock(Lock):
    """``redis.lock.Lock`` whose release avoids Lua (fakeredis has no Lua engine)."""

    def do_release(self, expected_token: bytes) -> None:
        with self.redis.pipeline() as pipe:
            try:
                pipe.watch(self.name)
                if _as_bytes(pipe.get(self.name)) != _as_bytes(expected_token):
                    pipe.unwatch()
                    raise LockNotOwnedError(
                        "Cannot release a lock that's no longer owned",
                        lock_name=self.name,
                    )
                pipe.multi()
                pipe.delete(self.name)
                pipe.execute()
            except WatchError as exc:
                raise LockNotOwnedError(
                    "Lock changed while releasing", lock_name=self.name
                ) from exc


class SessionStore:
    """JSON session blob + login generation counter for one provider."""

    def __init__(self, provider_key: str):
        self.provider_key = provider_key
        self.session_key = f"provider:session:{provider_key}"
        self.generation_key = f"provider:session-gen:{provider_key}"

    def load(self) -> dict | None:
        raw = get_redis().get(self.session_key)
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def save(self, data: dict) -> None:
        get_redis().set(
            self.session_key,
            json.dumps(data, ensure_ascii=False),
            ex=SESSION_TTL_SECONDS,
        )

    def clear(self) -> None:
        get_redis().delete(self.session_key)

    def generation(self) -> int:
        return int(get_redis().get(self.generation_key) or 0)

    def bump_generation(self) -> int:
        return int(get_redis().incr(self.generation_key))


def _cooldown_key(provider_key: str) -> str:
    return f"provider:login-cooldown:{provider_key}"


def _failures_key(provider_key: str) -> str:
    return f"provider:login-failures:{provider_key}"


def _start_cooldown(redis_client, provider_key: str) -> None:
    failures_key = _failures_key(provider_key)
    failures = int(redis_client.incr(failures_key))
    redis_client.expire(failures_key, LOGIN_FAILURES_TTL_SECONDS)
    seconds = min(
        LOGIN_COOLDOWN_SECONDS * 2 ** min(failures - 1, 10), LOGIN_COOLDOWN_MAX_SECONDS
    )
    redis_client.set(_cooldown_key(provider_key), "1", ex=seconds)


def run_authenticated(
    adapter: BaseAdapter,
    fn: Callable[[], T],
    lock_wait: float | None = None,
) -> T:
    """Call ``fn``; on ``AuthRequired`` log in once (cluster-wide) and retry once.

    The generation counter lets callers that waited on the lock skip the
    login another worker already performed while they were blocked. After a
    failed login no one retries until the cool-down expires. ``lock_wait``
    defaults to ``LOGIN_LOCK_WAIT_SECONDS`` (sized for web requests). A
    disabled provider account raises ``PermanentError`` before ``fn`` runs.
    """
    accounts.ensure_enabled(adapter.key)
    store = SessionStore(adapter.key)
    gen = store.generation()
    try:
        return fn()
    except AuthRequired:
        pass
    if lock_wait is None:
        lock_wait = LOGIN_LOCK_WAIT_SECONDS
    _login_once(adapter, store, gen, lock_wait)
    return fn()


def _login_once(
    adapter: BaseAdapter, store: SessionStore, seen_generation: int, lock_wait: float
) -> None:
    key = adapter.key
    redis_client = get_redis()
    if redis_client.exists(_cooldown_key(key)):
        raise TransientError("login_failed")
    lock = redis_client.lock(
        f"provider:login-lock:{key}",
        timeout=LOGIN_LOCK_TIMEOUT_SECONDS,
        blocking_timeout=lock_wait,
        lock_class=NoLuaLock,
    )
    if not lock.acquire():
        raise TransientError("login_busy")
    try:
        if store.generation() != seen_generation:
            return
        if redis_client.exists(_cooldown_key(key)):
            raise TransientError("login_failed")
        try:
            adapter.login()
        except Exception as exc:
            _start_cooldown(redis_client, key)
            error = scrub(str(exc)) or exc.__class__.__name__
            log.warning("provider %s login failed: %s", key, error)
            accounts.record_login(key, False, error)
            raise TransientError("login_failed") from exc
        store.bump_generation()
        redis_client.delete(_cooldown_key(key), _failures_key(key))
        accounts.record_login(key, True)
    finally:
        try:
            lock.release()
        except LockError:
            log.warning("provider %s login lock expired before release", key)
