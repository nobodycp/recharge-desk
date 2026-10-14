"""Asynchronous refresh: submit a job, poll its result, or wait for it.

Callers (public page, employee page, SMS) never block on the provider
login. They ``submit_refresh`` and either get an immediate answer
(validation, cooldown, ...) or a job id to poll with :func:`job_payload`.
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from redis.exceptions import LockError

from phone_refresh.models import RefreshSource, SystemSettings
from phone_refresh.services.refresh_service import (
    INTERNAL_RESULT_KEYS,
    complete_failure,
    internal_result_extras,
    normalize_source,
    precheck,
    refresh_phone,
    result_payload,
    status_payload,
)
from phone_refresh.tasks import _safe_notify, run_refresh_job
from providers import jobs
from providers.models import ProviderJob
from providers.redis_conn import get_redis
from providers.sessions import NoLuaLock

log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 0.5

SUBMIT_LOCK_TIMEOUT_SECONDS = 10
SUBMIT_LOCK_WAIT_SECONDS = 3

_FALLBACK_BUSY_PAYLOAD = {
    "status": "busy",
    "message": {"title": "الخدمة مشغولة", "body": "الخدمة مشغولة حالياً، حاول بعد قليل."},
}

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
    api_token_id: int | None = None,
) -> RefreshTicket:
    """Start (or join) a refresh for ``phone``.

    ``api_token_id`` marks the job as owned by that ``/api/v1`` token: it is
    stored in the job payload and such a job is only ever shared with
    submits carrying the same id (and token-less submits never join it).
    """
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

    source = normalize_source(source)
    wait = SystemSettings.get().refresh_wait_seconds
    # Only a plain customer submit may share a job, and only with another
    # plain one: notify targets, admin overrides and test runs must stay
    # on a job of their own.
    shareable = not (notify or forced_provider or internal_test or bypass_provider_off)

    job = None
    job_payload_ctx = {
        "ip": ip,
        "internal_test": internal_test,
        "forced_provider": forced_provider,
        "bypass_provider_off": bypass_provider_off,
        "notify": notify,
    }
    if api_token_id is not None:
        job_payload_ctx["api_token_id"] = api_token_id
    with _submit_lock(phone):
        if shareable:
            existing = jobs.find_inflight(
                ProviderJob.KIND_REFRESH,
                phone,
                wait,
                accept=lambda job: _is_plain_job(job, api_token_id),
            )
            if existing is not None:
                return RefreshTicket(str(existing.id), None)
        job = jobs.create_job(
            kind=ProviderJob.KIND_REFRESH,
            provider_key=provider_key,
            phone=phone,
            source=source,
            payload=job_payload_ctx,
        )
    enqueue_failure: dict = {}

    def _enqueue() -> None:
        try:
            run_refresh_job.delay(str(job.id))
        except Exception:  # noqa: BLE001 — broker/redis/OS errors; never leave a job queued
            log.exception("could not enqueue refresh job %s", job.id)
            busy = _busy_payload(
                phone=phone,
                provider_key=provider_key,
                ip=ip,
                source=source,
                internal_test=internal_test,
            )
            jobs.mark_failed(job, busy)
            enqueue_failure["payload"] = busy
            # The task never ran, so nobody else will tell a waiting caller
            # (e.g. an inbound SMS); the hooks are idempotent.
            _safe_notify(job, busy)

    # Outside a transaction this runs immediately, so ``enqueue_failure`` is
    # filled before we return; inside one the failed job is what pollers see.
    transaction.on_commit(_enqueue)
    if enqueue_failure:
        return RefreshTicket(None, enqueue_failure["payload"])
    return RefreshTicket(str(job.id), None)


def _is_plain_job(job: ProviderJob, api_token_id: int | None = None) -> bool:
    ctx = job.payload or {}
    if ctx.get("api_token_id") != api_token_id:
        # Token jobs are private to their token; token-less submits never
        # join them, and a token never joins someone else's job.
        return False
    return not (
        ctx.get("notify")
        or ctx.get("forced_provider")
        or ctx.get("internal_test")
        or ctx.get("bypass_provider_off")
    )


@contextmanager
def _submit_lock(phone: str):
    """Serialise find-or-create per phone; never blocks submits on Redis trouble."""
    lock = None
    acquired = False
    try:
        lock = get_redis().lock(
            f"refresh-submit:{phone}",
            timeout=SUBMIT_LOCK_TIMEOUT_SECONDS,
            blocking_timeout=SUBMIT_LOCK_WAIT_SECONDS,
            lock_class=NoLuaLock,
        )
        acquired = lock.acquire()
    except Exception:  # noqa: BLE001 — fall through and create the job anyway
        log.warning("refresh submit lock unavailable for %s", phone, exc_info=True)
    try:
        yield
    finally:
        if acquired:
            try:
                lock.release()
            except LockError:
                log.warning("refresh submit lock expired before release for %s", phone)


def _busy_payload(*, phone, provider_key, ip, source, internal_test) -> dict:
    try:
        return result_payload(
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
    except Exception:  # noqa: BLE001 — the fallback path must not raise
        log.exception("could not log enqueue failure for %s", phone)
        return dict(_FALLBACK_BUSY_PAYLOAD)


def unknown_job_payload() -> dict:
    """Generic "unknown or expired" answer (same as :func:`job_payload` gives)."""
    return dict(_EXPIRED_PAYLOAD)


def _load_refresh_job(job_id: str) -> ProviderJob | None:
    try:
        job = ProviderJob.objects.filter(pk=job_id).first()
    except (ValidationError, ValueError):
        return None
    # Only refresh jobs are pollable; other kinds (charge, ...) look unknown.
    if job is None or job.kind != ProviderJob.KIND_REFRESH:
        return None
    return job


def _payload_for(job: ProviderJob) -> dict:
    if job.status in (ProviderJob.STATUS_DONE, ProviderJob.STATUS_FAILED):
        if not job.result:
            return dict(_EXPIRED_PAYLOAD)
        # Admin-only diagnostics (internal tests) never leave this module
        # through the customer/employee status endpoints.
        return {k: v for k, v in job.result.items() if k not in INTERNAL_RESULT_KEYS}

    age = (timezone.now() - job.created_at).total_seconds()
    if age < SystemSettings.get().refresh_wait_seconds:
        return {"status": "processing", "job": str(job.id)}
    return status_payload("processing", final=True)


def job_payload(job_id: str, *, include_api_jobs: bool = False) -> dict:
    """Current customer-facing JSON for ``job_id`` (safe to expose).

    Jobs submitted through ``/api/v1`` belong to their token and look
    unknown here unless ``include_api_jobs`` is set (see
    :func:`token_job_payload`).
    """
    job = _load_refresh_job(job_id)
    if job is None:
        return dict(_EXPIRED_PAYLOAD)
    if not include_api_jobs and (job.payload or {}).get("api_token_id") is not None:
        return dict(_EXPIRED_PAYLOAD)
    return _payload_for(job)


def token_job_payload(job_id: str, api_token_id: int) -> dict | None:
    """Payload of a refresh job submitted by ``api_token_id``, else ``None``.

    ``None`` covers "not yours", "unknown" and "not a refresh job" alike (one
    lookup), so callers can answer all three identically.
    """
    job = _load_refresh_job(job_id)
    if job is None or (job.payload or {}).get("api_token_id") != api_token_id:
        return None
    return _payload_for(job)


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


@dataclass
class InternalTestOutcome:
    """What the admin test screens render: customer payload + raw diagnostics."""

    payload: dict
    raw_excerpt: str = ""
    diagnostics: dict | None = None

    def __post_init__(self) -> None:
        if self.diagnostics is None:
            self.diagnostics = {}


def run_internal_test(
    phone: str,
    *,
    forced_provider: str | None = None,
    bypass_provider_off: bool = False,
    timeout_seconds: float = 45,
) -> InternalTestOutcome:
    """Admin internal test: submit as a job and wait for it (never past ``timeout_seconds``).

    A job still running after the wait is reported with the final
    ``processing`` message; the job keeps going in the background.
    """
    kwargs = dict(
        internal_test=True,
        forced_provider=forced_provider,
        bypass_provider_off=bypass_provider_off,
    )
    if not settings.REFRESH_ASYNC_ENABLED:
        # Kill switch: run inline like before, so diagnostics are still available.
        result = refresh_phone(
            phone, ip=None, source=RefreshSource.INTERNAL_TEST, **kwargs
        )
        extras = internal_result_extras(result)
        return InternalTestOutcome(
            result_payload(result), extras["raw_excerpt"], extras["diagnostics"]
        )

    ticket = submit_refresh(
        phone, ip=None, source=RefreshSource.INTERNAL_TEST, **kwargs
    )
    if ticket.job_id is None:
        return InternalTestOutcome(ticket.payload)

    payload = wait_for_job(ticket.job_id, timeout_seconds)
    if payload.get("status") == "processing" and not payload.get("final"):
        payload = status_payload("processing", final=True)
    stored = _stored_result(ticket.job_id)
    return InternalTestOutcome(
        payload, stored.get("raw_excerpt") or "", stored.get("diagnostics") or {}
    )


def _stored_result(job_id: str) -> dict:
    job = ProviderJob.objects.filter(pk=job_id).first()
    if job is None or job.status not in (
        ProviderJob.STATUS_DONE,
        ProviderJob.STATUS_FAILED,
    ):
        return {}
    return job.result or {}
