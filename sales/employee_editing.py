"""Rules for employee self-service edit/delete on recent sales/payments."""

from __future__ import annotations

from customers.models import CustomerPaymentSubmission
from sales.models import Sale

EMPLOYEE_RECENT_EDIT_LIMIT = 10


def employee_editable_sale_ids(user) -> set[int]:
    """PKs of the employee's last N non-cancelled sales (newest first), minus provider-charged ones."""
    if not user or not getattr(user, "is_authenticated", False):
        return set()
    recent = (
        Sale.objects.filter(created_by=user)
        .exclude(status__in=(Sale.Status.CANCELLED, Sale.Status.WRITTEN_OFF))
        .order_by("-created_at", "-id")
        .values_list("pk", "entry_mode")[:EMPLOYEE_RECENT_EDIT_LIMIT]
    )
    return {pk for pk, mode in recent if mode != Sale.EntryMode.AUTO_CHARGE}


def sale_is_employee_editable(sale: Sale, user, editable_ids: set[int] | None = None) -> bool:
    if sale.created_by_id != user.id:
        return False
    if sale.status in (Sale.Status.CANCELLED, Sale.Status.WRITTEN_OFF):
        return False
    if sale.entry_mode == Sale.EntryMode.AUTO_CHARGE:
        return False
    if editable_ids is None:
        editable_ids = employee_editable_sale_ids(user)
    return sale.pk in editable_ids


def employee_editable_payment_submission_ids(user) -> set[int]:
    """PKs of the employee's last N payment submissions (newest first)."""
    if not user or not getattr(user, "is_authenticated", False):
        return set()
    return set(
        CustomerPaymentSubmission.objects.filter(created_by=user)
        .order_by("-created_at", "-id")
        .values_list("pk", flat=True)[:EMPLOYEE_RECENT_EDIT_LIMIT]
    )


def payment_submission_is_employee_editable(
    submission: CustomerPaymentSubmission,
    user,
    editable_ids: set[int] | None = None,
) -> bool:
    """Edit is only for awaiting rows still inside the last-N window."""
    if submission.created_by_id != user.id:
        return False
    if submission.status != CustomerPaymentSubmission.Status.AWAITING:
        return False
    if editable_ids is None:
        editable_ids = employee_editable_payment_submission_ids(user)
    return submission.pk in editable_ids


def payment_submission_is_employee_deletable(
    submission: CustomerPaymentSubmission,
    user,
    editable_ids: set[int] | None = None,
) -> bool:
    """Delete/undo within the last-N window (awaiting, approved with link, rejected)."""
    if submission.created_by_id != user.id:
        return False
    if editable_ids is None:
        editable_ids = employee_editable_payment_submission_ids(user)
    if submission.pk not in editable_ids:
        return False
    if submission.status == CustomerPaymentSubmission.Status.APPROVED:
        return submission.resulting_payment_id is not None
    return True
