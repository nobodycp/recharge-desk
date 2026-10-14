from __future__ import annotations

import datetime as dt
import logging

from celery import shared_task
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.utils import timezone

from providers import jobs, operations, service
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


NUMBER_INFO_BUDGET_SECONDS = 60


def _load_job(job_id: str) -> ProviderJob | None:
    try:
        return ProviderJob.objects.filter(pk=job_id).first()
    except (ValidationError, ValueError):
        return None


def _refresh_db_connection() -> None:
    """Drop a connection that went stale during the long provider call.

    Skipped in eager mode, where the task shares the caller's connection
    (and possibly its transaction).
    """
    if not settings.CELERY_TASK_ALWAYS_EAGER:
        close_old_connections()


@shared_task(
    time_limit=jobs.JOB_TASK_TIME_LIMIT, soft_time_limit=jobs.JOB_TASK_SOFT_TIME_LIMIT
)
def run_number_info_job(job_id: str) -> None:
    """Run one queued number-info job to completion; never raises.

    The service records the operation row itself, so a crash here only fails the
    job with the generic internal message (no second row). Only classified
    Arabic text is stored in the result, never raw provider text.
    """
    job = _load_job(job_id)
    if job is None:
        log.warning("number info job %s not found; ignoring", job_id)
        return
    if job.kind != ProviderJob.KIND_NUMBER_INFO:
        log.warning("job %s is a %s job, not a number info; ignoring", job.pk, job.kind)
        return
    if not jobs.mark_running(job):
        log.info("number info job %s already taken; skipping", job.pk)
        return

    try:
        payload = job.payload or {}
        result = service.number_info(
            job.provider_key,
            job.phone,
            NUMBER_INFO_BUDGET_SECONDS,
            source=job.source,
            user_id=payload.get("user_id"),
            sale_id=payload.get("sale_id"),
            job_id=job.pk,
        )
        if result.ok:
            outcome, ok = {"status": "ok", "info": result.info.to_dict()}, True
        else:
            outcome, ok = {
                "status": "error",
                "message": result.classified.message,
                "error_code": result.classified.error_code,
            }, False
    except Exception as exc:  # noqa: BLE001 — the worker task must never raise
        log.error("number info job %s failed: %s", job.pk, scrub(str(exc)), exc_info=True)
        classified = operations.classify_exception("number_info", exc)
        outcome = {
            "status": "error",
            "message": classified.message,
            "error_code": classified.error_code,
        }
        ok = False
    _refresh_db_connection()
    finished = jobs.mark_done(job, outcome) if ok else jobs.mark_failed(job, outcome)
    if not finished:
        log.info("number info job %s already finished elsewhere; result dropped", job.pk)


REFRESH_JOB_RETENTION_DAYS = 30


@shared_task
def purge_old_jobs() -> int:
    """Delete refresh and number-info jobs and provider operations past retention (beat, daily).

    Returns the number of jobs deleted; operations are logged.
    """
    now = timezone.now()
    deleted, _ = ProviderJob.objects.filter(
        kind__in=[ProviderJob.KIND_REFRESH, ProviderJob.KIND_NUMBER_INFO],
        created_at__lt=now - dt.timedelta(days=REFRESH_JOB_RETENTION_DAYS),
    ).delete()
    ops_deleted, _ = ProviderOperation.objects.filter(
        created_at__lt=now
        - dt.timedelta(days=settings.PROVIDER_OPERATIONS_RETENTION_DAYS)
    ).delete()
    log.info("purged %d provider operation rows past retention", ops_deleted)
    return deleted
