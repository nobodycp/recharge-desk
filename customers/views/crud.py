"""Top-level customer pages: list, create, edit, detail, delete.

Anything that mutates the customer **balance** (payments, adjustments,
write-offs) lives in :mod:`customers.views.actions` instead — keeping
the two responsibilities separate makes it much easier to find a given
piece of behaviour and to reason about its side effects.
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib import messages
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from accounts.permissions import management_required
from core.pagination import paginate_request
from customers.forms import (
    CustomerAdjustmentForm,
    CustomerForm,
    CustomerPaymentForm,
    CustomerPhoneForm,
)
from customers.models import Customer
from customers.services import create_customer, delete_customer_completely


@management_required
def customer_list(request):
    # prefetch_related("phones") flips the per-row "show first 3 phones"
    # from N+1 (one query per customer) to two queries total. Without it
    # a 50-customer page issues 51 queries just to render the badges.
    qs = Customer.objects.prefetch_related("phones")
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(phones__phone__icontains=q)).distinct()
    debt_total = (
        Customer.objects.filter(current_balance__gt=0)
        .aggregate(s=Sum("current_balance"))["s"]
        or Decimal("0")
    )
    credit_total = (
        Customer.objects.filter(current_balance__lt=0)
        .aggregate(s=Sum("current_balance"))["s"]
        or Decimal("0")
    )
    qs = qs.order_by("-current_balance", "name")
    page_obj = paginate_request(request, qs)
    return render(
        request,
        "customers/customer_list.html",
        {
            "page_obj": page_obj,
            "title": _("Customers"),
            "q": q,
            "debt_total": debt_total,
            "credit_total": credit_total,
        },
    )


@management_required
def customer_create(request):
    form = CustomerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            customer = create_customer(
                name=form.cleaned_data["name"],
                phones=[form.cleaned_data.get("initial_phone") or ""],
                notes=form.cleaned_data.get("notes") or "",
                user=request.user,
            )
            customer.is_active = form.cleaned_data["is_active"]
            customer.save(update_fields=["is_active"])
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Customer created."))
            return redirect("customers:customer_detail", pk=customer.pk)
    return render(
        request,
        "customers/customer_form.html",
        {"form": form, "title": _("New customer")},
    )


@management_required
def customer_edit(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    form = CustomerForm(request.POST or None, instance=customer)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Customer updated."))
        return redirect("customers:customer_detail", pk=customer.pk)
    return render(
        request,
        "customers/customer_form.html",
        {"form": form, "title": _("Edit customer"), "customer": customer},
    )


@management_required
def customer_detail(request, pk):
    customer = get_object_or_404(Customer, pk=pk)

    # Local import: customers <-> sales would be a circular import at the
    # module level, so we defer Sale until the request is being served.
    from sales.models import Sale

    on_account_qs = Sale.objects.filter(customer=customer, on_account=True)
    awaiting_count = on_account_qs.filter(status=Sale.Status.AWAITING).count()
    pending_count = on_account_qs.filter(status=Sale.Status.PENDING).count()

    sales_page = paginate_request(
        request,
        on_account_qs.select_related(
            "company", "product", "product__line", "payment_method", "created_by"
        ).order_by("-created_at"),
        page_param="sales_page",
    )
    ledger_page = paginate_request(
        request,
        customer.ledger_entries.select_related(
            "sale", "payment", "created_by"
        ).order_by("-created_at"),
        page_param="ledger_page",
    )
    phones_page = paginate_request(
        request,
        customer.phones.order_by("phone"),
        page_param="phones_page",
    )
    payments = customer.payments.select_related(
        "payment_method", "created_by"
    ).order_by("-created_at")[:200]

    from inventory.models import SimStockBalance

    sim_balances = (
        SimStockBalance.objects.filter(
            location=SimStockBalance.Location.CUSTOMER,
            customer=customer,
            quantity__gt=0,
        )
        .select_related("product_line")
        .order_by("product_line__name")
    )

    return render(
        request,
        "customers/customer_detail.html",
        {
            "title": customer.name,
            "customer": customer,
            "sales_page": sales_page,
            "ledger_page": ledger_page,
            "phones_page": phones_page,
            "payments": payments,
            "awaiting_count": awaiting_count,
            "pending_count": pending_count,
            "payment_form": CustomerPaymentForm(),
            "phone_form": CustomerPhoneForm(),
            "adjustment_form": CustomerAdjustmentForm(),
            "sim_balances": sim_balances,
        },
    )


@management_required
@require_POST
def customer_delete(request, pk):
    """Hard-delete a customer and everything attached. Used for QA cleanup."""
    customer = get_object_or_404(Customer, pk=pk)
    name = customer.name
    try:
        delete_customer_completely(customer=customer, user=request.user)
    except Exception as exc:  # pragma: no cover - defensive
        messages.error(request, str(exc))
        return redirect("customers:customer_detail", pk=pk)
    messages.success(
        request,
        _("Customer “%(name)s” and all linked records were deleted.")
        % {"name": name},
    )
    return redirect("customers:customer_list")
