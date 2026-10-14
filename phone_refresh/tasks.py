"""Celery tasks for the asynchronous refresh flow."""
from __future__ import annotations

import logging
import time

from celery import shared_task
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import close_old_connections

from phone_refresh.services.refresh_service import (
    complete,
    complete_failure,
    result_payload,
    status_payload,
)
from providers import jobs
from providers import service as provider_service
from providers.models import ProviderJob
from providers.sanitize import scrub

log = logging.getLogger(__name__)

# Workers are not bound by the web request timeout, so they may wait out a
# login lock and retry for much longer than the 45 s sync default.
WORKER_REFRESH_BUDGET_SECONDS = 150
# A job still queued/running after this long lost its worker (crash, restart).
STUCK_JOB_SECONDS = 600

_FALLBACK_ERROR_PAYLOAD = {
    "status": "error",
    "message": {"title": "خطأ", "body": "حدث خطأ غير متوقع. الرجاء المحاولة لاحقاً."},
}


def _error_payload() -> dict:
    try:
        return status_payload("error")
    except Exception:  # noqa: BLE001 — the failure path must never raise
        return dict(_FALLBACK_ERROR_PAYLOAD)


def _notify(job: ProviderJob, result: dict) -> None:
    """Completion hook for callers that asked to be told (e.g. inbound SMS).

    Intentionally a no-op until the SMS gateway wires it up; ``job.payload``
    already carries the ``notify`` context.
    """


def _safe_notify(job: ProviderJob, result: dict) -> None:
    try:
        _notify(job, result)
    except Exception:  # noqa: BLE001 — a notify failure must not undo a finished job
        log.exception("refresh job %s completion hook failed", job.pk)


def _refresh_db_connection() -> None:
    """Drop a connection that went stale during the long provider call.

    Skipped in eager mode, where the task shares the caller's connection
    (and possibly its transaction).
    """
    if not settings.CELERY_TASK_ALWAYS_EAGER:
        close_old_connections()


def _load_job(job_id: str) -> ProviderJob | None:
    try:
        return ProviderJob.objects.filter(pk=job_id).first()
    except (ValidationError, ValueError):
        return None


def _execute(job: ProviderJob) -> dict:
    ctx = job.payload or {}
    started = time.monotonic()
    common = dict(
        phone=job.phone,
        provider_key=job.provider_key,
        ip=ctx.get("ip"),
        source=job.source,
        started=started,
        internal_test=bool(ctx.get("internal_test")),
    )
    try:
        raw = provider_service.refresh(
            job.provider_key, job.phone, budget=WORKER_REFRESH_BUDGET_SECONDS
        )
    except Exception as exc:  # noqa: BLE001 — provider errors must not escape the worker
        log.exception("provider %s crashed for phone %s", job.provider_key, job.phone)
        return result_payload(complete_failure(error=str(exc), **common))
    return result_payload(complete(raw=raw, **common))


@shared_task
def run_refresh_job(job_id: str) -> None:
    """Run one queued refresh job to completion; never raises."""
    job = _load_job(job_id)
    if job is None:
        log.warning("refresh job %s not found; ignoring", job_id)
        return
    if job.kind != ProviderJob.KIND_REFRESH:
        log.warning("job %s is a %s job, not a refresh; ignoring", job.pk, job.kind)
        return
    if not jobs.mark_running(job):
        log.info("refresh job %s already taken; skipping", job.pk)
        return

    try:
        result = _execute(job)
    except Exception as exc:  # noqa: BLE001
        log.error("refresh job %s failed: %s", job.pk, scrub(str(exc)), exc_info=True)
        result = _error_payload()
        _refresh_db_connection()
        jobs.mark_failed(job, result)
    else:
        _refresh_db_connection()
        jobs.mark_done(job, result)
    _safe_notify(job, result)


@shared_task
def reap_stuck_jobs() -> int:
    """Fail refresh jobs whose worker vanished (beat, every 5 min); returns count.

    Pollers then see the generic error instead of ``processing`` forever, and
    SMS callers that asked to be notified are told.
    """
    reaped = jobs.fail_stuck(
        ProviderJob.KIND_REFRESH, STUCK_JOB_SECONDS, _error_payload()
    )
    for job in reaped:
        log.warning("refresh job %s was stuck in its queue/worker; failed it", job.pk)
        _safe_notify(job, job.result)
    return len(reaped)
