"""Gateway health snapshot for the providers settings page."""
from __future__ import annotations

import logging

from django.conf import settings

from providers.models import ProviderAccount
from providers.redis_conn import get_redis

log = logging.getLogger(__name__)


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
        for account in ProviderAccount.objects.order_by("provider_key")
    ]
    return {"redis_ok": _redis_ok(), "worker_ok": _worker_ok(), "accounts": accounts}
