from __future__ import annotations

import logging

from celery import shared_task

from providers import service
from providers.models import ProviderAccount
from providers.registry import ADAPTERS
from providers.sanitize import scrub

log = logging.getLogger(__name__)


@shared_task
def keepalive_sessions() -> None:
    """Keep login-based provider sessions warm (beat, every 5 min); never raises."""
    keys = ProviderAccount.objects.filter(is_enabled=True).values_list(
        "provider_key", flat=True
    )
    for key in keys:
        adapter_cls = ADAPTERS.get(key)
        if adapter_cls is None or not adapter_cls.needs_login:
            continue
        try:
            service.keepalive(key)
            log.info("provider %s keep-alive ok", key)
        except Exception as exc:
            log.warning("provider %s keep-alive failed: %s", key, scrub(str(exc)))
