"""Apply system settings after employee sales / payment actions."""

from __future__ import annotations

from core.models import AppSettings


def finalize_sale_after_entry(
    *, sale, user, on_account: bool, paid_via_employee: bool = False
) -> str:
    """Run auto-approval paths when management queues are disabled.

    Returns a short outcome token: ``pending_debt``, ``posted_debt``,
    ``pending_payment``, ``pending_employee_payment``, or ``paid``.
    """
    settings = AppSettings.load()
    if on_account:
        if settings.require_debt_request_approval:
            return "pending_debt"
        from customers.services import post_on_account_sale

        post_on_account_sale(sale=sale, user=user)
        return "posted_debt"

    if settings.require_payment_request_approval:
        if paid_via_employee:
            from sales.services import mark_sale_paid

            mark_sale_paid(sale=sale, user=user)
            return "paid_employee"
        return "pending_payment"

    from sales.services import mark_sale_paid

    mark_sale_paid(sale=sale, user=user)
    if paid_via_employee:
        return "paid_employee"
    return "paid"


def finalize_payment_submission_after_entry(*, submission, user) -> bool:
    """Return True when the submission was applied immediately.

    Employee-held payments always apply immediately (same as sales
    ``paid_via_employee``), even when settlement approval is enabled.
    """
    if (
        AppSettings.load().require_settlement_request_approval
        and not submission.paid_via_employee
    ):
        return False
    from customers.services import approve_customer_payment_submission

    approve_customer_payment_submission(submission=submission, user=user)
    return True
