from __future__ import annotations

import logging

from celery import shared_task

from providers import service
from providers.accounts import get_credentials
from providers.contracts import PermanentError
from providers.models import ProviderAccount
from providers.registry import ADAPTERS
from providers.sanitize import scrub

log = logging.getLogger(__name__)


@shared_task
def keepalive_sessions() -> None:
    """Keep login-based provider sessions warm (beat, every 5 min); never raises.

    Covers every ``needs_login`` adapter with usable credentials (DB account or
    env fallback), except providers whose DB account is disabled.
    """
    disabled = set(
        ProviderAccount.objects.filter(is_enabled=False).values_list(
            "provider_key", flat=True
        )
    )
    for key, adapter_cls in ADAPTERS.items():
        if not adapter_cls.needs_login or key in disabled:
            continue
        try:
            get_credentials(key)
        except PermanentError:
            continue
        try:
            service.keepalive(key)
            log.info("provider %s keep-alive ok", key)
        except Exception as exc:
            log.warning("provider %s keep-alive failed: %s", key, scrub(str(exc)))
