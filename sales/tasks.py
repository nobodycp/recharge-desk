"""Celery tasks for charge-and-record sales. No task ever retries a charge."""
from __future__ import annotations

import datetime as dt
import logging
from time import monotonic

from celery import shared_task
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import close_old_connections, transaction
from django.db.models import Q
from django.utils import timezone

from providers import jobs
from providers import service as provider_service
from providers.contracts import Capability, ChargeOutcome, ChargeResult
from providers.models import ProviderJob
from providers.redis_conn import get_redis
from providers.registry import ADAPTERS
from providers.sanitize import scrub
from providers.sessions import NoLuaLock
from sales import charging
from sales.models import Sale

log = logging.getLogger(__name__)

# Workers are not bound by the web request timeout (see phone_refresh.tasks).
WORKER_CHARGE_BUDGET_SECONDS = 150
WORKER_CHECK_BUDGET_SECONDS = 150
# A processing sale younger than this is left to its own job.
PROCESSING_GRACE = dt.timedelta(minutes=5)
# The beat skips sales checked more recently than this.
RECHECK_AFTER = dt.timedelta(minutes=5)
# One verify run stops starting checks after this; the lock outlives the last check.
VERIFY_RUN_BUDGET_SECONDS = 240
VERIFY_LOCK_KEY = "sales:verify-charges:lock"
VERIFY_LOCK_TTL_SECONDS = 600


def _error_text(exc: Exception) -> str:
    return scrub(str(exc)) or exc.__class__.__name__


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


def _claim(job: ProviderJob) -> Sale | None:
    """Take the job only while its sale is locked and still awaiting a charge.

    ``manual_resolve`` holds the same lock, so a claimed job and a manual
    resolution exclude each other.
    """
    with transaction.atomic():
        sale = (
            Sale.objects.select_for_update()
            .filter(pk=(job.payload or {}).get("sale_id"))
            .first()
        )
        if sale is None:
            refusal = "sale_missing"
        elif sale.status == Sale.Status.CANCELLED:
            refusal = "sale_cancelled"
        elif sale.charge_status != Sale.ChargeStatus.PROCESSING:
            refusal = "sale_not_processing"
        else:
            refusal = None
        if refusal is not None:
            if jobs.fail_if_queued(job, {"error": refusal}):
                log.warning("charge job %s refused (%s); not charging", job.pk, refusal)
            else:
                log.info("charge job %s already taken or finished; skipping", job.pk)
            return None
        if not jobs.mark_running(job):
            log.info("charge job %s already taken or finished; skipping", job.pk)
            return None
        return sale


def _charge(job: ProviderJob, sale: Sale) -> ChargeOutcome:
    try:
        ctx = charging.charge_context(sale, job)
    except Exception as exc:  # noqa: BLE001 — nothing was sent
        log.error("charge job %s: could not build the charge request: %s", job.pk, _error_text(exc))
        return ChargeOutcome(result=ChargeResult.FAILED, message="not_sent:context_error")
    try:
        return provider_service.charge(job.provider_key, ctx, budget=WORKER_CHARGE_BUDGET_SECONDS)
    except KeyError:
        return ChargeOutcome(result=ChargeResult.FAILED, message="not_sent:unknown_provider")
    except Exception as exc:  # noqa: BLE001 — the request may have gone out
        error = _error_text(exc)
        log.error("charge job %s crashed: %s", job.pk, error)
        return ChargeOutcome(result=ChargeResult.UNVERIFIED, message=error)


@shared_task
def run_charge_job(job_id: str) -> None:
    """Send the one charge for a queued job and record the outcome; never raises."""
    job = _load_job(job_id)
    if job is None or job.kind != ProviderJob.KIND_CHARGE:
        log.warning("charge job %s not found or not a charge; ignoring", job_id)
        return
    sale = _claim(job)
    if sale is None:
        return

    outcome = _charge(job, sale)
    _refresh_db_connection()
    try:
        charging.apply_outcome(sale.pk, outcome)
    except Exception as exc:  # noqa: BLE001 — the verify task checks the sale later
        log.error(
            "charge job %s: could not record %s outcome: %s",
            job.pk,
            outcome.result.value,
            _error_text(exc),
        )
        jobs.mark_failed(job, _job_result(outcome))
        return
    jobs.mark_done(job, _job_result(outcome))


def _can_check(provider_key: str) -> bool:
    adapter_cls = ADAPTERS.get(provider_key)
    return adapter_cls is not None and Capability.CHECK_CHARGE in adapter_cls.capabilities


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
            and job.started_at > timezone.now() - charging.RUNNING_STALE_AFTER
        ):
            return
    if not _can_check(job.provider_key):
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


def _verify_due_sales() -> None:
    now = timezone.now()
    deadline = monotonic() + VERIFY_RUN_BUDGET_SECONDS
    sale_ids = list(
        Sale.objects.filter(
            Q(charge_status=Sale.ChargeStatus.PROCESSING, created_at__lt=now - PROCESSING_GRACE)
            | Q(charge_status=Sale.ChargeStatus.UNVERIFIED),
            Q(charge_checked_at__isnull=True) | Q(charge_checked_at__lt=now - RECHECK_AFTER),
            charge_job_id__isnull=False,
        )
        .order_by("created_at")
        .values_list("pk", flat=True)
    )
    for index, sale_id in enumerate(sale_ids):
        if monotonic() >= deadline:
            log.info("charge verification budget spent; %s sales left", len(sale_ids) - index)
            return
        try:
            _settle(sale_id)
        except Exception as exc:  # noqa: BLE001 — one sale must not block the rest
            log.error("charge verification for sale %s failed: %s", sale_id, _error_text(exc))


@shared_task
def verify_unverified_charges() -> None:
    """Settle open charges (beat, every 2 min); single-flight, never raises."""
    try:
        lock = get_redis().lock(
            VERIFY_LOCK_KEY, timeout=VERIFY_LOCK_TTL_SECONDS, lock_class=NoLuaLock
        )
        if not lock.acquire(blocking=False):
            log.info("charge verification already running; skipping")
            return
    except Exception as exc:  # noqa: BLE001 — without the lock, skip this run
        log.error("charge verification skipped, Redis unavailable: %s", _error_text(exc))
        return
    try:
        _verify_due_sales()
    finally:
        try:
            lock.release()
        except Exception as exc:  # noqa: BLE001 — the lock expires on its own
            log.warning("charge verification lock not released: %s", _error_text(exc))


@shared_task
def check_sale_charge(sale_id: int) -> None:
    """Management "recheck": settle one open charge now; never raises."""
    try:
        _settle(sale_id)
    except Exception as exc:  # noqa: BLE001
        log.error("charge check for sale %s failed: %s", sale_id, _error_text(exc))
