"""Persistence helpers for :class:`providers.models.ProviderJob`."""
from __future__ import annotations

import datetime as _dt
from typing import Callable

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


def fail_if_queued(job: ProviderJob, result: dict) -> bool:
    """Atomically move ``queued`` → ``failed``; False if a worker already took it."""
    now = timezone.now()
    updated = ProviderJob.objects.filter(
        pk=job.pk, status=ProviderJob.STATUS_QUEUED
    ).update(status=ProviderJob.STATUS_FAILED, result=result, finished_at=now)
    if updated != 1:
        return False
    job.status = ProviderJob.STATUS_FAILED
    job.result = result
    job.finished_at = now
    return True


def find_inflight(
    kind: str,
    phone: str,
    max_age_seconds: int,
    accept: Callable[[ProviderJob], bool] | None = None,
) -> ProviderJob | None:
    """Newest queued/running job for ``kind``+``phone`` created within the window.

    ``accept`` lets the caller skip jobs it must not share (checked newest first).
    """
    cutoff = timezone.now() - _dt.timedelta(seconds=max_age_seconds)
    candidates = ProviderJob.objects.filter(
        kind=kind,
        phone=phone,
        status__in=[ProviderJob.STATUS_QUEUED, ProviderJob.STATUS_RUNNING],
        created_at__gte=cutoff,
    ).order_by("-created_at")
    for job in candidates:
        if accept is None or accept(job):
            return job
    return None


def fail_stuck(kind: str, older_than_seconds: int, result: dict) -> list[ProviderJob]:
    """Fail jobs that never finished; returns the ones this call actually failed.

    ``running`` jobs are judged by ``started_at``, ``queued`` ones by
    ``created_at``. Each job is flipped with a status-guarded update so a
    worker that finishes concurrently is never overwritten.
    """
    cutoff = timezone.now() - _dt.timedelta(seconds=older_than_seconds)
    stuck = list(
        ProviderJob.objects.filter(
            kind=kind, status=ProviderJob.STATUS_RUNNING, started_at__lt=cutoff
        )
    ) + list(
        ProviderJob.objects.filter(
            kind=kind, status=ProviderJob.STATUS_QUEUED, created_at__lt=cutoff
        )
    )
    reaped = []
    now = timezone.now()
    for job in stuck:
        updated = ProviderJob.objects.filter(pk=job.pk, status=job.status).update(
            status=ProviderJob.STATUS_FAILED, result=result, finished_at=now
        )
        if updated == 1:
            job.status = ProviderJob.STATUS_FAILED
            job.result = result
            job.finished_at = now
            reaped.append(job)
    return reaped
