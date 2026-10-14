"""Charge-and-record sale: record the sale, have the provider charge it, settle safely.

Money rules: a charge is sent at most once (never retried), a sale whose
charge may have gone through is never reversed, and a sale whose charge
definitely did not happen is reversed exactly once.
"""
from __future__ import annotations

import logging

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from audit.models import AuditAction
from audit.services import record as audit_record
from core.sale_workflow import finalize_sale_after_entry
from providers import jobs
from providers.contracts import Capability, ChargeContext, ChargeOutcome, ChargeResult
from providers.models import ProviderAccount, ProviderJob
from providers.registry import ADAPTERS
from providers.sanitize import scrub
from providers.service import company_supports
from sales.models import Sale
from sales.services import _cancellation_ledger_exists, cancel_sale, create_sale

log = logging.getLogger(__name__)

OPEN_STATUSES = (Sale.ChargeStatus.PROCESSING, Sale.ChargeStatus.UNVERIFIED)

_STATUS_FOR_RESULT = {
    ChargeResult.SUCCESS: Sale.ChargeStatus.SUCCESS,
    ChargeResult.FAILED: Sale.ChargeStatus.FAILED,
    ChargeResult.UNVERIFIED: Sale.ChargeStatus.UNVERIFIED,
}


def _provider_key(company) -> str:
    return (company.phone_refresh_provider or "").strip().lower()


def can_charge(company) -> bool:
    """The company's provider can charge and, if it logs in, its account is enabled."""
    if not company_supports(company, Capability.CHARGE):
        return False
    key = _provider_key(company)
    if not ADAPTERS[key].needs_login:
        return True
    return ProviderAccount.objects.filter(provider_key=key, is_enabled=True).exists()


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
    )


def _job_for_token(form_token: str) -> ProviderJob | None:
    return ProviderJob.objects.filter(idempotency_key=form_token).first()


def _sale_for_job(job: ProviderJob) -> Sale:
    sale = Sale.objects.filter(charge_job_id=job.pk).first()
    if sale is None:
        raise ValueError(_("This form was already submitted."))
    return sale


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


def _record_and_queue(form_token: str, user, create_sale_kwargs: dict) -> Sale:
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
    finalize_sale_after_entry(
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
    return sale


def create_charged_sale(*, form_token: str, user, **create_sale_kwargs) -> Sale:
    """Record the sale (deducting cost as usual) and queue one provider charge.

    Resubmitting the same ``form_token`` returns the original sale, also when
    two submits race. Raises ``ValueError`` like ``create_sale``.
    """
    form_token = (form_token or "").strip()
    if not form_token:
        raise ValueError(_("This form has expired. Reload the page and try again."))
    existing = _job_for_token(form_token)
    if existing is not None:
        return _sale_for_job(existing)
    if not can_charge(create_sale_kwargs["company"]):
        raise ValueError(_("This company cannot be charged through its provider."))
    try:
        with transaction.atomic():
            return _record_and_queue(form_token, user, create_sale_kwargs)
    except IntegrityError:
        existing = _job_for_token(form_token)
        if existing is None:
            raise
        return _sale_for_job(existing)


def apply_outcome(sale_id: int, outcome: ChargeOutcome, *, actor=None) -> Sale:
    """Record a charge outcome on an open sale; ``FAILED`` reverses it once.

    Sales already ``success``/``failed`` (or never charged) are left untouched.
    """
    with transaction.atomic():
        sale = Sale.objects.select_for_update().get(pk=sale_id)
        previous = sale.charge_status
        new_status = _STATUS_FOR_RESULT[outcome.result]
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
        sale.save(
            update_fields=[
                "charge_status",
                "charge_checked_at",
                "charge_message",
                "charge_provider_ref",
                "charge_plan_name",
                "charge_provider_cost",
                "updated_at",
            ]
        )
        audit_record(
            AuditAction.UPDATE,
            sale,
            actor=actor,
            extra={"charge_status": new_status, "previous_charge_status": previous},
        )

        if new_status == Sale.ChargeStatus.FAILED and sale.status != Sale.Status.CANCELLED:
            if _cancellation_ledger_exists(sale.pk):
                log.warning("sale %s already has a cancellation reversal; not reversing", sale.pk)
            else:
                sale = cancel_sale(sale=sale, user=actor)
        return sale


def manual_resolve(sale: Sale, *, success: bool, user) -> Sale:
    """Management settles an open charge by hand (failure reverses the sale)."""
    with transaction.atomic():
        locked = Sale.objects.select_for_update().get(pk=sale.pk)
        if locked.charge_status not in OPEN_STATUSES:
            raise ValueError(_("Only charges in progress or unverified can be resolved."))
        result = ChargeResult.SUCCESS if success else ChargeResult.FAILED
        outcome = ChargeOutcome(result=result, message=f"manual:{result.value}")
        return apply_outcome(locked.pk, outcome, actor=user)


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
