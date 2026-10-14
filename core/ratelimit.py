"""Redis-backed fixed-window rate limiter shared by every web worker."""
from __future__ import annotations

import logging

from providers.redis_conn import get_redis

log = logging.getLogger(__name__)


def allow(bucket: str, ident: str, limit: int, window_seconds: int) -> bool:
    """Count one hit for ``ident`` in ``bucket``; return whether it is within ``limit``.

    Fixed window: the window starts at the first hit and the key expires
    ``window_seconds`` later; later hits never push the expiry back. A
    ``limit`` of ``0`` disables the check, an empty ``ident`` is never
    limited, and Redis trouble fails open (logged) so customers are not
    locked out by an infrastructure fault.
    """
    if not limit or not ident:
        return True
    key = f"ratelimit:{bucket}:{ident}"
    try:
        redis = get_redis()
        pipe = redis.pipeline()
        pipe.incr(key)
        pipe.ttl(key)
        count, ttl = pipe.execute()
        if count == 1 or ttl < 0:
            # First hit of the window (or a counter that somehow lost its
            # expiry): start the window now. Only this path costs a 2nd call.
            redis.expire(key, window_seconds)
    except Exception:  # noqa: BLE001 — never block customers on limiter failure
        log.warning("rate limiter unavailable for %s", bucket, exc_info=True)
        return True
    return count <= limit
