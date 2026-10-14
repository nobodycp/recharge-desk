"""Persistence helpers for :class:`providers.models.ProviderJob`."""
from __future__ import annotations

import datetime as _dt

from django.db.models import F
from django.utils import timezone

from providers.models import ProviderJob


def create_job(
    *,
    kind: str,
    provider_key: str,
    phone: str,
    source: str,
    payload: dict | None = None,
    idempotency_key: str | None = None,
) -> ProviderJob:
    return ProviderJob.objects.create(
        kind=kind,
        provider_key=provider_key,
        phone=phone,
        source=source,
        payload=payload or {},
        idempotency_key=idempotency_key,
    )


def mark_running(job: ProviderJob) -> bool:
    """Atomically move ``queued`` → ``running``; False if someone else took it."""
    now = timezone.now()
    taken = ProviderJob.objects.filter(
        pk=job.pk, status=ProviderJob.STATUS_QUEUED
    ).update(
        status=ProviderJob.STATUS_RUNNING,
        started_at=now,
        attempts=F("attempts") + 1,
    )
    if taken != 1:
        return False
    job.status = ProviderJob.STATUS_RUNNING
    job.started_at = now
    job.attempts += 1
    return True


def _finish(job: ProviderJob, status: str, result: dict) -> None:
    now = timezone.now()
    ProviderJob.objects.filter(pk=job.pk).update(
        status=status, result=result, finished_at=now
    )
    job.status = status
    job.result = result
    job.finished_at = now


def mark_done(job: ProviderJob, result: dict) -> None:
    _finish(job, ProviderJob.STATUS_DONE, result)


def mark_failed(job: ProviderJob, result: dict) -> None:
    _finish(job, ProviderJob.STATUS_FAILED, result)


def find_inflight(kind: str, phone: str, max_age_seconds: int) -> ProviderJob | None:
    """Newest queued/running job for ``kind``+``phone`` created within the window."""
    cutoff = timezone.now() - _dt.timedelta(seconds=max_age_seconds)
    return (
        ProviderJob.objects.filter(
            kind=kind,
            phone=phone,
            status__in=[ProviderJob.STATUS_QUEUED, ProviderJob.STATUS_RUNNING],
            created_at__gte=cutoff,
        )
        .order_by("-created_at")
        .first()
    )
