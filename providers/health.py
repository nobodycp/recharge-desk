"""Gateway health snapshot for the providers settings page."""
from __future__ import annotations

import datetime as dt
import logging

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from providers.models import ProviderConnection, ProviderJob
from providers.redis_conn import get_redis

log = logging.getLogger(__name__)

# A refresh job still queued after this long means no worker is consuming it.
REFRESH_QUEUE_STALE_SECONDS = 120
REFRESH_QUEUE_ALERT_CACHE_KEY = "providers:refresh-queue-stalled"
REFRESH_QUEUE_ALERT_CACHE_TTL = 30


def refresh_queue_stalled() -> bool:
    """True when a refresh job has waited in the queue past the stale limit (cached briefly)."""
    stalled = cache.get(REFRESH_QUEUE_ALERT_CACHE_KEY)
    if stalled is None:
        cutoff = timezone.now() - dt.timedelta(seconds=REFRESH_QUEUE_STALE_SECONDS)
        stalled = ProviderJob.objects.filter(
            kind=ProviderJob.KIND_REFRESH,
            status=ProviderJob.STATUS_QUEUED,
            created_at__lt=cutoff,
        ).exists()
        cache.set(
            REFRESH_QUEUE_ALERT_CACHE_KEY, stalled, timeout=REFRESH_QUEUE_ALERT_CACHE_TTL
        )
    return stalled


def _redis_ok() -> bool:
    try:
        return bool(get_redis().ping())
    except Exception:
        log.warning("Redis ping failed", exc_info=True)
        return False


def _worker_ok() -> bool:
    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        return True
    from config.celery import app

    try:
        return bool(app.control.ping(timeout=1))
    except Exception:
        log.warning("Celery worker ping failed", exc_info=True)
        return False


def status() -> dict:
    accounts = [
        {
            "key": account.provider_key,
            "last_login_at": account.last_login_at,
            "last_login_ok": account.last_login_ok,
        }
        for account in ProviderConnection.objects.order_by("provider_key")
    ]
    return {"redis_ok": _redis_ok(), "worker_ok": _worker_ok(), "accounts": accounts}


ALERTS_CACHE_KEY = "providers:health-alerts"
ALERTS_CACHE_TTL = 30

ALERT_REDIS_DOWN = "Redis لا يستجيب"
ALERT_WORKER_DOWN = "الـ worker متوقف أو الطلبات عالقة"
ALERT_LOGIN_FAILED = "فشل تسجيل الدخول: {company}"


def _connection_tab_url(company) -> str:
    from django.urls import reverse

    return reverse("companies:company_detail", args=[company.pk]) + "?tab=connection"


def alerts() -> list:
    """Dashboard "حالة الربط" items: ``[{"text", "url"}]``; empty when healthy.

    Cached for 30 s so the dashboard doesn't ping Redis/Celery on every load.
    Redis/worker items link to the connection tab of the first company (by pk)
    that has a provider system, or have an empty url when there is none.
    """
    cached = cache.get(ALERTS_CACHE_KEY)
    if cached is not None:
        return cached

    from companies.models import Company

    companies = Company.objects.exclude(provider_key="").order_by("pk")
    first = companies.first()
    health_url = _connection_tab_url(first) if first else ""

    items = []
    if not _redis_ok():
        items.append({"text": ALERT_REDIS_DOWN, "url": health_url})
    if not _worker_ok() or refresh_queue_stalled():
        items.append({"text": ALERT_WORKER_DOWN, "url": health_url})

    failed_keys = ProviderConnection.objects.filter(
        is_enabled=True, last_login_ok=False
    ).order_by("provider_key").values_list("provider_key", flat=True)
    for key in failed_keys:
        company = companies.filter(provider_key=key).first()
        if company is None:
            continue
        items.append(
            {
                "text": ALERT_LOGIN_FAILED.format(company=company.name),
                "url": _connection_tab_url(company),
            }
        )

    cache.set(ALERTS_CACHE_KEY, items, timeout=ALERTS_CACHE_TTL)
    return items
