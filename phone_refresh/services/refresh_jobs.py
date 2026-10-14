"""Asynchronous refresh: submit a job, poll its result, or wait for it.

Callers (public page, employee page, SMS) never block on the provider
login. They ``submit_refresh`` and either get an immediate answer
(validation, cooldown, ...) or a job id to poll with :func:`job_payload`.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import redis.exceptions
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from kombu.exceptions import OperationalError

from phone_refresh.models import RefreshSource, SystemSettings
from phone_refresh.services.refresh_service import (
    complete_failure,
    precheck,
    refresh_phone,
    result_payload,
    status_payload,
)
from phone_refresh.tasks import run_refresh_job
from providers import jobs
from providers.models import ProviderJob

log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 0.5

_ENQUEUE_ERRORS = (OperationalError, redis.exceptions.ConnectionError, OSError)

_EXPIRED_PAYLOAD = {
    "status": "error",
    "message": {"title": "خطأ", "body": "انتهت صلاحية الطلب، أعد المحاولة."},
}


@dataclass
class RefreshTicket:
    """Either a ready ``payload`` (no job needed) or a ``job_id`` to poll."""

    job_id: str | None
    payload: dict | None


def submit_refresh(
    phone: str,
    *,
    ip: str | None,
    source: str = RefreshSource.WEB,
    internal_test: bool = False,
    forced_provider: str | None = None,
    bypass_provider_off: bool = False,
    notify: dict | None = None,
) -> RefreshTicket:
    if not settings.REFRESH_ASYNC_ENABLED:
        result = refresh_phone(
            phone,
            ip=ip,
            internal_test=internal_test,
            forced_provider=forced_provider,
            bypass_provider_off=bypass_provider_off,
            source=source,
        )
        return RefreshTicket(None, result_payload(result))

    phone = (phone or "").strip()
    immediate, provider_key = precheck(
        phone,
        ip=ip,
        internal_test=internal_test,
        forced_provider=forced_provider,
        bypass_provider_off=bypass_provider_off,
        source=source,
    )
    if immediate is not None:
        return RefreshTicket(None, result_payload(immediate))

    wait = SystemSettings.get().refresh_wait_seconds
    existing = jobs.find_inflight(ProviderJob.KIND_REFRESH, phone, wait)
    if existing is not None:
        return RefreshTicket(str(existing.id), None)

    job = jobs.create_job(
        kind=ProviderJob.KIND_REFRESH,
        provider_key=provider_key,
        phone=phone,
        source=source,
        payload={
            "ip": ip,
            "internal_test": internal_test,
            "forced_provider": forced_provider,
            "bypass_provider_off": bypass_provider_off,
            "notify": notify,
        },
    )
    enqueue_failure: dict = {}

    def _enqueue() -> None:
        try:
            run_refresh_job.delay(str(job.id))
        except _ENQUEUE_ERRORS:
            log.exception("could not enqueue refresh job %s", job.id)
            busy = result_payload(
                complete_failure(
                    phone=phone,
                    provider_key=provider_key,
                    error="enqueue_failed",
                    ip=ip,
                    source=source,
                    started=time.monotonic(),
                    internal_test=internal_test,
                    status_code="busy",
                )
            )
            jobs.mark_failed(job, busy)
            enqueue_failure["payload"] = busy

    # Outside a transaction this runs immediately, so ``enqueue_failure`` is
    # filled before we return; inside one the failed job is what pollers see.
    transaction.on_commit(_enqueue)
    if enqueue_failure:
        return RefreshTicket(None, enqueue_failure["payload"])
    return RefreshTicket(str(job.id), None)


def job_payload(job_id: str) -> dict:
    """Current customer-facing JSON for ``job_id`` (safe to expose)."""
    try:
        job = ProviderJob.objects.filter(pk=job_id).first()
    except (ValidationError, ValueError):
        job = None
    if job is None:
        return dict(_EXPIRED_PAYLOAD)

    if job.status in (ProviderJob.STATUS_DONE, ProviderJob.STATUS_FAILED):
        return job.result or dict(_EXPIRED_PAYLOAD)

    age = (timezone.now() - job.created_at).total_seconds()
    if age < SystemSettings.get().refresh_wait_seconds:
        return {"status": "processing", "job": str(job.id)}
    return status_payload("processing", final=True)


def wait_for_job(job_id: str, timeout_seconds: float = 45) -> dict:
    """Poll ``job_payload`` until it settles or ``timeout_seconds`` pass."""
    if settings.CELERY_TASK_ALWAYS_EAGER:
        # No worker in dev/tests: run it here. ``mark_running`` makes the
        # deferred on_commit enqueue a no-op, so it cannot run twice.
        try:
            queued = ProviderJob.objects.filter(
                pk=job_id, status=ProviderJob.STATUS_QUEUED
            ).exists()
        except (ValidationError, ValueError):
            queued = False
        if queued:
            run_refresh_job(job_id)

    deadline = time.monotonic() + timeout_seconds
    while True:
        payload = job_payload(job_id)
        if payload.get("status") != "processing" or payload.get("final"):
            return payload
        if time.monotonic() >= deadline:
            return payload
        time.sleep(POLL_INTERVAL_SECONDS)
