"""Celery tasks for charge-and-record sales. No task ever retries a charge."""
from __future__ import annotations

import datetime as dt
import logging

from celery import shared_task
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.db.models import Q
from django.utils import timezone

from providers import jobs
from providers import service as provider_service
from providers.contracts import ChargeOutcome, ChargeResult
from providers.models import ProviderJob
from providers.sanitize import scrub
from sales import charging
from sales.models import Sale

log = logging.getLogger(__name__)

# Workers are not bound by the web request timeout (see phone_refresh.tasks).
WORKER_CHARGE_BUDGET_SECONDS = 150
WORKER_CHECK_BUDGET_SECONDS = 150
# A processing sale younger than this is left to its own job.
PROCESSING_GRACE = dt.timedelta(minutes=5)
# A charge job running longer than this lost its worker.
RUNNING_STALE_AFTER = dt.timedelta(minutes=10)


def _refresh_db_connection() -> None:
    """Drop a connection that went stale during the long provider call (not in eager mode)."""
    if not settings.CELERY_TASK_ALWAYS_EAGER:
        close_old_connections()


def _load_job(job_id: str) -> ProviderJob | None:
    try:
        return ProviderJob.objects.filter(pk=job_id).first()
    except (ValidationError, ValueError):
        return None


def _load_sale(sale_id) -> Sale | None:
    if sale_id is None:
        return None
    return Sale.objects.select_related("product").filter(pk=sale_id).first()


def _job_result(outcome: ChargeOutcome) -> dict:
    return {
        "result": outcome.result.value,
        "provider_ref": outcome.provider_ref[:100],
        "message": charging.clean_message(outcome.message),
    }


@shared_task
def run_charge_job(job_id: str) -> None:
    """Send the one charge for a queued job and record the outcome; never raises."""
    job = _load_job(job_id)
    if job is None or job.kind != ProviderJob.KIND_CHARGE:
        log.warning("charge job %s not found or not a charge; ignoring", job_id)
        return
    if not jobs.mark_running(job):
        log.info("charge job %s already taken; skipping", job.pk)
        return

    sale = _load_sale((job.payload or {}).get("sale_id"))
    if sale is None or sale.charge_status != Sale.ChargeStatus.PROCESSING:
        log.warning("charge job %s: sale is no longer awaiting a charge; not charging", job.pk)
        jobs.mark_failed(job, {"error": "sale_not_processing"})
        return

    ctx = charging.charge_context(sale, job)
    try:
        outcome = provider_service.charge(
            job.provider_key, ctx, budget=WORKER_CHARGE_BUDGET_SECONDS
        )
    except KeyError:
        outcome = ChargeOutcome(result=ChargeResult.FAILED, message="not_sent:unknown_provider")
    except Exception as exc:  # noqa: BLE001 — the request may have gone out
        error = scrub(str(exc)) or exc.__class__.__name__
        log.error("charge job %s crashed: %s", job.pk, error)
        outcome = ChargeOutcome(result=ChargeResult.UNVERIFIED, message=error)

    _refresh_db_connection()
    try:
        charging.apply_outcome(sale.pk, outcome)
    except Exception as exc:  # noqa: BLE001 — the verify task checks the sale later
        log.error(
            "charge job %s: could not record %s outcome: %s",
            job.pk,
            outcome.result.value,
            scrub(str(exc)) or exc.__class__.__name__,
        )
        jobs.mark_failed(job, _job_result(outcome))
        return
    jobs.mark_done(job, _job_result(outcome))


def _settle(sale_id: int) -> None:
    """Move one open charge forward without ever sending a new charge."""
    sale = _load_sale(sale_id)
    if sale is None or sale.charge_status not in charging.OPEN_STATUSES:
        return
    job = _load_job(str(sale.charge_job_id)) if sale.charge_job_id else None
    if job is None:
        log.warning("sale %s has no charge job; needs manual resolution", sale.pk)
        return
    if sale.charge_status == Sale.ChargeStatus.PROCESSING:
        if job.status == ProviderJob.STATUS_QUEUED:
            run_charge_job.delay(str(job.pk))
            return
        if (
            job.status == ProviderJob.STATUS_RUNNING
            and job.started_at is not None
            and job.started_at > timezone.now() - RUNNING_STALE_AFTER
        ):
            return

    outcome = provider_service.check_charge(
        job.provider_key,
        charging.charge_context(sale, job),
        sale.charge_provider_ref,
        budget=WORKER_CHECK_BUDGET_SECONDS,
    )
    _refresh_db_connection()
    if outcome.result == ChargeResult.UNVERIFIED:
        Sale.objects.filter(pk=sale.pk).update(charge_checked_at=timezone.now())
        return
    charging.apply_outcome(sale.pk, outcome)


@shared_task
def verify_unverified_charges() -> None:
    """Settle open charges (beat, every 2 min); never raises."""
    now = timezone.now()
    sale_ids = list(
        Sale.objects.filter(
            Q(charge_status=Sale.ChargeStatus.PROCESSING, created_at__lt=now - PROCESSING_GRACE)
            | Q(charge_status=Sale.ChargeStatus.UNVERIFIED),
            charge_job_id__isnull=False,
        )
        .order_by("created_at")
        .values_list("pk", flat=True)
    )
    for sale_id in sale_ids:
        try:
            _settle(sale_id)
        except Exception as exc:  # noqa: BLE001 — one sale must not block the rest
            log.error(
                "charge verification for sale %s failed: %s",
                sale_id,
                scrub(str(exc)) or exc.__class__.__name__,
            )


@shared_task
def check_sale_charge(sale_id: int) -> None:
    """Management "recheck": settle one open charge now; never raises."""
    try:
        _settle(sale_id)
    except Exception as exc:  # noqa: BLE001
        log.error(
            "charge check for sale %s failed: %s",
            sale_id,
            scrub(str(exc)) or exc.__class__.__name__,
        )
