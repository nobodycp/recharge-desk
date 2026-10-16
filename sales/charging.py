"""Charge-and-record sale: record the sale, have the provider charge it, settle safely.

Money rules: a charge is sent at most once (never retried), a sale whose
charge may have gone through is never reversed, and a sale whose charge
definitely did not happen is reversed exactly once.
"""
from __future__ import annotations

import datetime as dt
import logging

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.permissions import is_management
from audit.models import AuditAction
from audit.services import record as audit_record
from core.sale_workflow import finalize_sale_after_entry
from providers import jobs
from providers.contracts import Capability, ChargeContext, ChargeOutcome, ChargeResult
from providers.models import ProviderJob
from providers.sanitize import scrub
from providers.service import service_state
from sales.models import Sale
from sales.services import _cancellation_ledger_exists, cancel_sale, create_sale

log = logging.getLogger(__name__)

OPEN_STATUSES = (Sale.ChargeStatus.PROCESSING, Sale.ChargeStatus.UNVERIFIED)
DUPLICATE_SUBMIT = "duplicate"
# A charge job running longer than this lost its worker (worker budget is 150 s).
RUNNING_STALE_AFTER = dt.timedelta(minutes=10)

_STATUS_FOR_RESULT = {
    ChargeResult.SUCCESS: Sale.ChargeStatus.SUCCESS,
    ChargeResult.FAILED: Sale.ChargeStatus.FAILED,
    ChargeResult.UNVERIFIED: Sale.ChargeStatus.UNVERIFIED,
}


def _provider_key(company) -> str:
    return (company.provider_key or "").strip().lower()


def can_charge(company) -> bool:
    """The company's provider can charge: supported, configured, and switched on."""
    return service_state(company, Capability.CHARGE).code == "active"


def user_may_charge(user) -> bool:
    """Management always may. An employee may only after management grants it."""
    if is_management(user):
        return True
    profile = getattr(user, "profile", None)
    return bool(profile and profile.can_provider_charge)


def clean_message(message: str) -> str:
    return scrub(message or "")[:255]


def charge_context(sale: Sale, job: ProviderJob) -> ChargeContext:
    return ChargeContext(
        sale_id=sale.pk,
        phone=sale.reference_number,
        expected_cost=sale.cost_price_snapshot,
        product_name=sale.product.display_name,
        is_esim=sale.is_esim,
        idempotency_key=job.idempotency_key or "",
        requested_at=sale.created_at,
        duration_days=sale.duration_days,
    )


def _job_for_token(form_token: str) -> ProviderJob | None:
    return ProviderJob.objects.filter(idempotency_key=form_token).first()


def _sale_for_job(job: ProviderJob, user) -> Sale:
    """The sale ``user`` recorded with this job's token; anyone else's is not revealed."""
    sale = Sale.objects.filter(charge_job_id=job.pk, created_by=user).first()
    if sale is None:
        raise ValueError(_("This form was already submitted."))
    return sale


def find_duplicate_submit(form_token: str, user) -> Sale | None:
    """The sale ``user`` already recorded with ``form_token``, or ``None`` if it is unused.

    Raises ``ValueError`` when the token is missing or was used by someone else.
    """
    form_token = (form_token or "").strip()
    if not form_token:
        raise ValueError(_("This form has expired. Reload the page and try again."))
    existing = _job_for_token(form_token)
    return _sale_for_job(existing, user) if existing is not None else None


def _enqueue_charge(job_id) -> None:
    from sales.tasks import run_charge_job

    try:
        run_charge_job.delay(str(job_id))
    except Exception as exc:  # noqa: BLE001 — the verify task re-enqueues queued jobs
        log.error(
            "charge job %s could not be enqueued (verify task will retry): %s",
            job_id,
            scrub(str(exc)) or exc.__class__.__name__,
        )


def _record_and_queue(form_token: str, user, create_sale_kwargs: dict) -> tuple[Sale, str]:
    company = create_sale_kwargs["company"]
    reference = (create_sale_kwargs.get("reference_number") or "").strip()
    # Job first: a concurrent duplicate fails on the unique key before any deduction.
    job = jobs.create_job(
        kind=ProviderJob.KIND_CHARGE,
        provider_key=_provider_key(company),
        phone=reference[:20],
        source="employee",
        idempotency_key=form_token,
    )
    sale = create_sale(user=user, **create_sale_kwargs)
    outcome = finalize_sale_after_entry(
        sale=sale,
        user=user,
        on_account=bool(create_sale_kwargs.get("on_account")),
        paid_via_employee=bool(create_sale_kwargs.get("paid_via_employee")),
    )
    ProviderJob.objects.filter(pk=job.pk).update(payload={"sale_id": sale.pk})
    sale.entry_mode = Sale.EntryMode.AUTO_CHARGE
    sale.charge_status = Sale.ChargeStatus.PROCESSING
    sale.charge_job_id = job.pk
    sale.save(update_fields=["entry_mode", "charge_status", "charge_job_id", "updated_at"])
    transaction.on_commit(lambda: _enqueue_charge(job.pk))
    sale.refresh_from_db()
    return sale, outcome


def create_charged_sale(
    *, form_token: str, user, **create_sale_kwargs
) -> tuple[Sale, str]:
    """Record the sale (deducting cost as usual) and queue one provider charge.

    Returns ``(sale, outcome)``: ``outcome`` is the ``finalize_sale_after_entry``
    token, or ``DUPLICATE_SUBMIT`` when this ``form_token`` was already used
    (also when two submits race) and ``sale`` is the original sale. Raises
    ``ValueError`` like ``create_sale``.
    """
    duplicate = find_duplicate_submit(form_token, user)
    if duplicate is not None:
        return duplicate, DUPLICATE_SUBMIT
    form_token = form_token.strip()
    if not can_charge(create_sale_kwargs["company"]):
        raise ValueError(_("This company cannot be charged through its provider."))
    try:
        with transaction.atomic():
            return _record_and_queue(form_token, user, create_sale_kwargs)
    except IntegrityError:
        existing = _job_for_token(form_token)
        if existing is None:
            raise
        return _sale_for_job(existing, user), DUPLICATE_SUBMIT


def _conflict_extra(outcome: ChargeOutcome) -> dict:
    return {
        "charge_conflict": "success_after_reversal",
        "provider_ref": outcome.provider_ref[:100],
        "plan_name": outcome.plan_name[:200],
        "provider_cost": outcome.provider_cost,
    }


def _flag_success_after_failure(sale: Sale, outcome: ChargeOutcome, actor, who: dict) -> Sale:
    log.error(
        "sale %s: provider reports a successful charge after the sale was reversed",
        sale.pk,
    )
    sale.charge_conflict = True
    sale.charge_checked_at = timezone.now()
    sale.save(update_fields=["charge_conflict", "charge_checked_at", "updated_at"])
    audit_record(
        AuditAction.UPDATE,
        sale,
        actor=actor,
        extra={"charge_status": sale.charge_status, **_conflict_extra(outcome), **who},
    )
    return sale


def apply_outcome(
    sale_id: int, outcome: ChargeOutcome, *, actor=None, audit_extra: dict | None = None
) -> Sale:
    """Record a charge outcome on an open sale; ``FAILED`` reverses it once.

    Sales already ``success``/``failed`` (or never charged) keep their status.
    A ``SUCCESS`` arriving after the sale was reversed never undoes the
    reversal: the sale gets ``charge_conflict`` for management to settle.
    ``actor=None`` means the system (worker / verify task).
    """
    who = {} if actor is not None else {"actor": "system"}
    with transaction.atomic():
        sale = Sale.objects.select_for_update().get(pk=sale_id)
        previous = sale.charge_status
        new_status = _STATUS_FOR_RESULT[outcome.result]
        if previous == Sale.ChargeStatus.FAILED and new_status == Sale.ChargeStatus.SUCCESS:
            return _flag_success_after_failure(sale, outcome, actor, who)
        if previous not in OPEN_STATUSES:
            level = logging.INFO if previous == new_status else logging.WARNING
            log.log(
                level,
                "sale %s charge is %s; ignoring %s outcome",
                sale.pk,
                previous or "not charged",
                outcome.result.value,
            )
            return sale

        sale.charge_status = new_status
        sale.charge_checked_at = timezone.now()
        sale.charge_message = clean_message(outcome.message)
        if outcome.provider_ref:
            sale.charge_provider_ref = outcome.provider_ref[:100]
        if outcome.plan_name:
            sale.charge_plan_name = outcome.plan_name[:200]
        if outcome.provider_cost is not None:
            sale.charge_provider_cost = outcome.provider_cost
        extra = {
            "charge_status": new_status,
            "previous_charge_status": previous,
            **who,
            **(audit_extra or {}),
        }
        if new_status == Sale.ChargeStatus.SUCCESS and sale.status == Sale.Status.CANCELLED:
            log.error(
                "sale %s: provider reports a successful charge but the sale is cancelled",
                sale.pk,
            )
            sale.charge_conflict = True
            extra.update(_conflict_extra(outcome))
        sale.save(
            update_fields=[
                "charge_status",
                "charge_checked_at",
                "charge_message",
                "charge_provider_ref",
                "charge_plan_name",
                "charge_provider_cost",
                "charge_conflict",
                "updated_at",
            ]
        )
        audit_record(AuditAction.UPDATE, sale, actor=actor, extra=extra)

        if new_status == Sale.ChargeStatus.FAILED and sale.status != Sale.Status.CANCELLED:
            if _cancellation_ledger_exists(sale.pk):
                log.warning("sale %s already has a cancellation reversal; not reversing", sale.pk)
            else:
                sale = cancel_sale(sale=sale, user=actor, audit_extra=who or None)
        return sale


class ChargeInProgress(ValueError):
    """The provider is still working on the charge; it cannot be resolved by hand yet."""


class ChargeNotSent(ValueError):
    """The charge was never sent, so it cannot be marked successful."""


def _stop_pending_job(sale: Sale, *, success: bool) -> None:
    """Keep the worker from charging a sale that is being resolved by hand.

    Caller holds the sale lock, which the worker also takes to claim the job.
    """
    job = ProviderJob.objects.filter(pk=sale.charge_job_id).first() if sale.charge_job_id else None
    if job is None:
        return
    if job.status == ProviderJob.STATUS_QUEUED:
        if success:
            raise ChargeNotSent(
                _("This charge was never sent to the provider. Resolve it as failed instead.")
            )
        if not jobs.fail_if_queued(job, {"error": "manually_resolved"}):
            job.refresh_from_db()
    if (
        job.status == ProviderJob.STATUS_RUNNING
        and job.started_at is not None
        and job.started_at > timezone.now() - RUNNING_STALE_AFTER
    ):
        raise ChargeInProgress(
            _("The provider is still processing this charge. Try again in a few minutes.")
        )


def manual_resolve(sale: Sale, *, success: bool, user) -> Sale:
    """Management settles an open charge by hand (failure reverses the sale).

    Raises ``ChargeInProgress`` while a worker is (recently) charging it,
    ``ChargeNotSent`` for ``success=True`` on a charge that was never sent,
    and ``ValueError`` when the charge is not open.
    """
    with transaction.atomic():
        locked = Sale.objects.select_for_update().get(pk=sale.pk)
        if not locked.charge_is_open:
            raise ValueError(_("Only charges in progress or unverified can be resolved."))
        _stop_pending_job(locked, success=success)
        result = ChargeResult.SUCCESS if success else ChargeResult.FAILED
        outcome = ChargeOutcome(result=result, message=f"manual:{result.value}")
        return apply_outcome(
            locked.pk,
            outcome,
            actor=user,
            audit_extra={"previous_charge_message": locked.charge_message},
        )


def recheck(sale: Sale) -> None:
    """Queue a provider check of this sale's charge (never a new charge)."""
    from sales.tasks import check_sale_charge

    try:
        check_sale_charge.delay(sale.pk)
    except Exception as exc:  # noqa: BLE001 — the beat check still runs
        log.error(
            "charge check for sale %s could not be enqueued: %s",
            sale.pk,
            scrub(str(exc)) or exc.__class__.__name__,
        )
