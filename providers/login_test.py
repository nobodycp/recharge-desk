"""Login test for a provider connection, run by the worker (never in a request).

``start`` only enqueues ``providers.tasks.test_connection``; the adapter login
happens on the Celery worker. The start time lives in the Django cache for
``TEST_TTL_SECONDS`` so the UI can tell "still running" from "finished":
a test is pending while that marker exists and ``last_login_at`` is older than it.
A second, longer-lived start time outlives the marker so the UI can tell a test
that expired without any result ("the worker never answered") from a finished one.
"""
from __future__ import annotations

import logging

from django.core.cache import cache
from django.utils import timezone

from providers.models import ProviderConnection

log = logging.getLogger(__name__)

TEST_TTL_SECONDS = 150
STARTED_TTL_SECONDS = 24 * 3600


def _cache_key(provider_key: str) -> str:
    return f"providers:login-test:{provider_key}"


def _started_key(provider_key: str) -> str:
    return f"providers:login-test-started:{provider_key}"


def _account(provider_key: str) -> ProviderConnection | None:
    return ProviderConnection.objects.filter(provider_key=provider_key).first()


def _is_pending(started_at: float | None, last_login_at) -> bool:
    if started_at is None:
        return False
    return last_login_at is None or last_login_at.timestamp() < started_at


def start(provider_key: str) -> str:
    """Enqueue a login test: ``"started"``, ``"pending"`` or ``"unavailable"``."""
    from providers import tasks

    key = _cache_key(provider_key)
    now = timezone.now().timestamp()
    # Claim the slot atomically; only the winner enqueues.
    if not cache.add(key, now, TEST_TTL_SECONDS):
        account = _account(provider_key)
        started_at = cache.get(key)
        if _is_pending(started_at, account.last_login_at if account else None):
            return "pending"
        # Stale marker (a login was recorded since it, or it just expired): take over.
        cache.set(key, now, TEST_TTL_SECONDS)
    cache.set(_started_key(provider_key), now, STARTED_TTL_SECONDS)
    try:
        tasks.test_connection.delay(provider_key)
    except Exception as exc:
        log.warning("provider %s login test could not be enqueued: %s", provider_key, exc)
        cache.delete(key)
        cache.delete(_started_key(provider_key))
        return "unavailable"
    return "started"


def status(provider_key: str) -> dict:
    """Latest login result plus whether a test is running or expired unanswered."""
    account = _account(provider_key)
    last_login_at = account.last_login_at if account else None
    pending = _is_pending(cache.get(_cache_key(provider_key)), last_login_at)
    return {
        "pending": pending,
        "expired": not pending
        and _is_pending(cache.get(_started_key(provider_key)), last_login_at),
        "last_login_at": last_login_at,
        "last_login_ok": account.last_login_ok if account else None,
        "last_error": account.last_error if account else "",
    }
