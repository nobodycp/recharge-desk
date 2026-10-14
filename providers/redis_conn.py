"""Process-wide Redis client.

Uses a real client when ``settings.REDIS_URL`` is set; otherwise a single
in-process ``fakeredis.FakeRedis`` (dev / tests), so callers never branch.
"""

from __future__ import annotations

import threading

import redis
from django.conf import settings

_lock = threading.Lock()
_client: redis.Redis | None = None
_client_url: str | None = None


def get_redis() -> redis.Redis:
    global _client, _client_url
    url = getattr(settings, "REDIS_URL", "") or ""
    with _lock:
        if _client is None or _client_url != url:
            if url:
                _client = redis.Redis.from_url(url)
            else:
                import fakeredis

                _client = fakeredis.FakeRedis()
            _client_url = url
        return _client
