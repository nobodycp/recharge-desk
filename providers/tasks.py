from __future__ import annotations

import datetime as dt
import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from providers import service
from providers.accounts import get_credentials, record_login
from providers.contracts import PermanentError
from providers.models import ProviderConnection, ProviderJob, ProviderOperation
from providers.registry import ADAPTERS
from providers.sanitize import scrub
from providers.sessions import reset_session

log = logging.getLogger(__name__)


@shared_task
def keepalive_sessions() -> None:
    """Keep login-based provider sessions warm (beat, every 5 min); never raises.

    Covers every ``needs_login`` adapter with usable credentials (DB account or
    env fallback), except providers whose DB account is disabled.
    """
    disabled = set(
        ProviderConnection.objects.filter(is_enabled=False).values_list(
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


@shared_task
def test_connection(provider_key: str) -> None:
    """Admin login test: fresh session, then one authenticated ping; never raises.

    Routed to the ``refresh`` queue by ``providers.tasks.*`` in
    ``CELERY_TASK_ROUTES``. A real login records its own result; if none was
    recorded (missing credentials, busy lock, Redis down, ...) the failure is
    recorded here so the UI never stays "pending".
    """
    started = timezone.now()
    error = ""
    try:
        reset_session(provider_key)
        service.keepalive(provider_key)
    except Exception as exc:
        error = scrub(str(exc)) or exc.__class__.__name__
        log.warning("provider %s login test failed: %s", provider_key, error)
    try:
        account = ProviderConnection.objects.filter(provider_key=provider_key).first()
        if account is not None and (
            account.last_login_at is None or account.last_login_at < started
        ):
            record_login(provider_key, not error, error)
    except Exception as exc:
        log.warning(
            "provider %s login test result not recorded: %s", provider_key, scrub(str(exc))
        )


REFRESH_JOB_RETENTION_DAYS = 30


@shared_task
def purge_old_jobs() -> int:
    """Delete refresh jobs and provider operations past retention (beat, daily).

    Returns the number of rows deleted.
    """
    now = timezone.now()
    deleted, _ = ProviderJob.objects.filter(
        kind=ProviderJob.KIND_REFRESH,
        created_at__lt=now - dt.timedelta(days=REFRESH_JOB_RETENTION_DAYS),
    ).delete()
    ops_deleted, _ = ProviderOperation.objects.filter(
        created_at__lt=now
        - dt.timedelta(days=settings.PROVIDER_OPERATIONS_RETENTION_DAYS)
    ).delete()
    return deleted + ops_deleted
