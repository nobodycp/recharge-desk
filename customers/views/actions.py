"""POST-only actions that mutate a customer's balance, ledger or phones.

Each view here corresponds to a button on ``customer_detail.html``. The
HTMX-aware delete endpoints reuse the row-removal helpers from
:mod:`sales.views._shared` so a successful action fades the row out in
place rather than triggering a full page reload — same UX as the sales
list and pending payments queue.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from accounts.permissions import management_required
from customers.forms import (
    CustomerAdjustmentForm,
    CustomerPaymentForm,
    CustomerPhoneForm,
)
from customers.models import Customer, CustomerLedger, CustomerPayment, CustomerPhone
from customers.services import (
    add_customer_phone,
    delete_customer_payment,
    delete_ledger_entry,
    record_customer_adjustment,
    record_customer_payment,
    write_off_customer_balance,
)
from customers.views._shared import flash_form_errors
from sales.views._shared import htmx_action_error, htmx_remove_target, is_htmx


@management_required
@require_POST
def customer_record_payment(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    form = CustomerPaymentForm(request.POST)
    if not form.is_valid():
        flash_form_errors(request, form)
        return redirect("customers:customer_detail", pk=customer.pk)
    try:
        record_customer_payment(
            customer=customer,
            amount=form.cleaned_data["amount"],
            payment_method=form.cleaned_data["payment_method"],
            notes=form.cleaned_data.get("notes") or "",
            user=request.user,
        )
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, _("Payment recorded."))
    return redirect("customers:customer_detail", pk=customer.pk)


@management_required
@require_POST
def customer_record_adjustment(request, pk):
    """Manual debit/credit on the customer balance — no sale, no profit/loss."""
    customer = get_object_or_404(Customer, pk=pk)
    form = CustomerAdjustmentForm(request.POST)
    if not form.is_valid():
        flash_form_errors(request, form)
        return redirect("customers:customer_detail", pk=customer.pk)
    try:
        record_customer_adjustment(
            customer=customer,
            amount=form.signed_amount(),
            notes=form.cleaned_data.get("notes") or "",
            user=request.user,
        )
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, _("Adjustment recorded."))
    return redirect("customers:customer_detail", pk=customer.pk)


@management_required
@require_POST
def customer_write_off(request, pk):
    """Convert all unpaid on-account sales for the customer into a loss."""
    customer = get_object_or_404(Customer, pk=pk)
    try:
        result = write_off_customer_balance(customer=customer, user=request.user)
    except Exception as exc:  # pragma: no cover - defensive
        messages.error(request, str(exc))
        return redirect("customers:customer_detail", pk=customer.pk)
    messages.success(
        request,
        _(
            "Account closed. %(count)d sale(s) written off · loss recorded: "
            "%(loss)s · debt cleared: %(debt)s."
        )
        % {
            "count": result["sales_written_off"],
            "loss": f"{result['loss_total']:.2f}",
            "debt": f"{result['debt_cleared']:.2f}",
        },
    )
    return redirect("customers:customer_detail", pk=customer.pk)


def _htmx_payment_deleted_response(*, payment_id: int, ledger_row_ids: list[int]) -> HttpResponse:
    """Strip matching ledger + recent-payment rows via HTMX out-of-band delete."""
    parts = [
        f'<tr id="customer-ledger-row-{lid}" hx-swap-oob="delete"></tr>'
        for lid in ledger_row_ids
    ]
    parts.append(
        f'<tr id="customer-payment-row-{payment_id}" hx-swap-oob="delete"></tr>'
    )
    return HttpResponse("".join(parts), status=200)


@management_required
@require_POST
def customer_payment_delete(request, pk, payment_id):
    """Remove a recorded customer payment and undo its FIFO settlements."""
    customer = get_object_or_404(Customer, pk=pk)
    payment = get_object_or_404(CustomerPayment, pk=payment_id, customer=customer)
    htmx = is_htmx(request)
    ledger_row_ids = list(
        CustomerLedger.objects.filter(
            customer_id=customer.pk, payment_id=payment_id
        ).values_list("pk", flat=True)
    )
    try:
        delete_customer_payment(payment=payment, user=request.user)
    except ValueError as exc:
        if htmx:
            return htmx_action_error(str(exc))
        messages.error(request, str(exc))
        return redirect("customers:customer_detail", pk=customer.pk)
    if htmx:
        return _htmx_payment_deleted_response(
            payment_id=payment_id, ledger_row_ids=ledger_row_ids
        )
    messages.success(request, _("Payment removed and settlements reversed."))
    return redirect("customers:customer_detail", pk=customer.pk)


@management_required
@require_POST
def customer_ledger_delete(request, pk, ledger_id):
    """Remove a single ledger row and undo its balance impact.

    Targeted clean-up for orphan rows (e.g. a CHARGE whose sale was
    permanently deleted before the delete-sale flow learned to reverse
    the customer ledger).
    """
    customer = get_object_or_404(Customer, pk=pk)
    entry = get_object_or_404(CustomerLedger, pk=ledger_id, customer=customer)
    htmx = is_htmx(request)
    try:
        delete_ledger_entry(entry=entry, user=request.user)
    except ValueError as exc:
        if htmx:
            return htmx_action_error(str(exc))
        messages.error(request, str(exc))
        return redirect("customers:customer_detail", pk=customer.pk)
    if htmx:
        return htmx_remove_target()
    messages.success(request, _("Ledger entry removed."))
    return redirect("customers:customer_detail", pk=customer.pk)


@management_required
@require_POST
def customer_add_phone(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    form = CustomerPhoneForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("Phone is required."))
        return redirect("customers:customer_detail", pk=customer.pk)
    try:
        add_customer_phone(
            customer=customer,
            phone=form.cleaned_data["phone"],
            label=form.cleaned_data.get("label") or "",
        )
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, _("Phone added."))
    return redirect("customers:customer_detail", pk=customer.pk)


@management_required
@require_POST
def customer_remove_phone(request, pk, phone_id):
    customer = get_object_or_404(Customer, pk=pk)
    phone = get_object_or_404(CustomerPhone, pk=phone_id, customer=customer)
    phone.delete()
    messages.success(request, _("Phone removed."))
    return redirect("customers:customer_detail", pk=customer.pk)
