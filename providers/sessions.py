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
LOGIN_LOCK_TIMEOUT_SECONDS = 180
LOGIN_LOCK_WAIT_SECONDS = 150


class _LoginLock(Lock):
    """``redis.lock.Lock`` whose release avoids Lua (fakeredis has no Lua engine)."""

    def do_release(self, expected_token: bytes) -> None:
        with self.redis.pipeline() as pipe:
            try:
                pipe.watch(self.name)
                if pipe.get(self.name) != expected_token:
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


def run_authenticated(adapter: BaseAdapter, fn: Callable[[], T]) -> T:
    """Call ``fn``; on ``AuthRequired`` log in once (cluster-wide) and retry once.

    The generation counter lets callers that waited on the lock skip the
    login another worker already performed while they were blocked.
    """
    store = SessionStore(adapter.key)
    gen = store.generation()
    try:
        return fn()
    except AuthRequired:
        pass
    _login_once(adapter, store, gen)
    return fn()


def _login_once(adapter: BaseAdapter, store: SessionStore, seen_generation: int) -> None:
    key = adapter.key
    lock = get_redis().lock(
        f"provider:login-lock:{key}",
        timeout=LOGIN_LOCK_TIMEOUT_SECONDS,
        blocking_timeout=LOGIN_LOCK_WAIT_SECONDS,
        lock_class=_LoginLock,
    )
    if not lock.acquire():
        raise TransientError("login_lock_timeout")
    try:
        if store.generation() != seen_generation:
            return
        try:
            adapter.login()
        except Exception as exc:
            error = scrub(str(exc)) or exc.__class__.__name__
            log.warning("provider %s login failed: %s", key, error)
            accounts.record_login(key, False, error)
            raise TransientError("login_failed") from exc
        store.bump_generation()
        accounts.record_login(key, True)
    finally:
        try:
            lock.release()
        except LockError:
            log.warning("provider %s login lock expired before release", key)
