"""Redis cache backend that degrades to "no cache" while Redis is unreachable."""
from __future__ import annotations

import logging
import time
from functools import wraps

from django.core.cache.backends.base import DEFAULT_TIMEOUT
from django.core.cache.backends.redis import RedisCache
from redis.exceptions import RedisError

log = logging.getLogger(__name__)

CACHE_ERRORS = (RedisError, OSError)
# After a failure Redis is skipped for this long, so an unreachable host costs
# one socket timeout per window instead of one per cache call.
CIRCUIT_OPEN_SECONDS = 10


def _fail_soft(fallback):
    """Run the wrapped call; on a Redis/socket error log and return ``fallback(args)``."""

    def decorator(method):
        @wraps(method)
        def wrapper(self, *args, **kwargs):
            if time.monotonic() < self._circuit_open_until:
                return fallback(*args, **kwargs)
            try:
                return method(self, *args, **kwargs)
            except CACHE_ERRORS as exc:
                self._circuit_open_until = time.monotonic() + CIRCUIT_OPEN_SECONDS
                log.warning(
                    "cache %s skipped, Redis unavailable (bypassed for %s s): %s",
                    method.__name__,
                    CIRCUIT_OPEN_SECONDS,
                    exc,
                )
                return fallback(*args, **kwargs)

        return wrapper

    return decorator


def _raise_missing(key, *args, **kwargs):
    # Callers treat ValueError from incr as "key missing" and fall back to set().
    raise ValueError(f"Key '{key}' not found")


class FailSoftRedisCache(RedisCache):
    """Reads are misses and writes are no-ops while Redis is down.

    The cache only ever holds derived data (KPIs, badge counts, singleton
    rows), so a Redis outage must slow pages down, not fail sales or pages.
    """

    _circuit_open_until = 0.0

    def close_circuit(self) -> None:
        """Try Redis again on the next call."""
        self._circuit_open_until = 0.0

    @_fail_soft(lambda key, default=None, version=None: default)
    def get(self, key, default=None, version=None):
        return super().get(key, default, version)

    @_fail_soft(lambda keys, version=None: {})
    def get_many(self, keys, version=None):
        return super().get_many(keys, version)

    @_fail_soft(lambda key, version=None: False)
    def has_key(self, key, version=None):
        return super().has_key(key, version)

    @_fail_soft(lambda key, value, timeout=DEFAULT_TIMEOUT, version=None: False)
    def add(self, key, value, timeout=DEFAULT_TIMEOUT, version=None):
        return super().add(key, value, timeout, version)

    @_fail_soft(lambda key, value, timeout=DEFAULT_TIMEOUT, version=None: None)
    def set(self, key, value, timeout=DEFAULT_TIMEOUT, version=None):
        return super().set(key, value, timeout, version)

    @_fail_soft(lambda data, timeout=DEFAULT_TIMEOUT, version=None: list(data))
    def set_many(self, data, timeout=DEFAULT_TIMEOUT, version=None):
        return super().set_many(data, timeout, version)

    @_fail_soft(lambda key, timeout=DEFAULT_TIMEOUT, version=None: False)
    def touch(self, key, timeout=DEFAULT_TIMEOUT, version=None):
        return super().touch(key, timeout, version)

    @_fail_soft(lambda key, version=None: False)
    def delete(self, key, version=None):
        return super().delete(key, version)

    @_fail_soft(lambda keys, version=None: None)
    def delete_many(self, keys, version=None):
        return super().delete_many(keys, version)

    @_fail_soft(_raise_missing)
    def incr(self, key, delta=1, version=None):
        return super().incr(key, delta, version)

    @_fail_soft(lambda: False)
    def clear(self):
        return super().clear()
