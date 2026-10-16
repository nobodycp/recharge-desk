"""Shared queryset helpers for sale listings."""

import datetime as dt
from typing import Any, Dict, Optional

from django.db.models import Q
from django.utils import timezone

from sales.models import Sale

# ``charge_status`` filter value for open charges management must look at.
NEEDS_VERIFICATION = "needs_verification"
CHARGE_ESCALATE_AFTER = dt.timedelta(minutes=30)


def needs_charge_verification_q() -> Q:
    """Charges still open after the escalation window, or flagged as a conflict."""
    cutoff = timezone.now() - CHARGE_ESCALATE_AFTER
    return Q(
        charge_status__in=(Sale.ChargeStatus.PROCESSING, Sale.ChargeStatus.UNVERIFIED),
        created_at__lt=cutoff,
    ) | Q(charge_conflict=True)


# Statuses that must NOT contribute to volume / profit / KPI aggregates.
# AWAITING sales are credit-sales pending management approval — they are
# real shipments but not yet acknowledged by the shop, so they sit in a
# holding bay until approved or rejected.
EXCLUDED_AGGREGATE_STATUSES = (
    Sale.Status.CANCELLED,
    Sale.Status.AWAITING,
    Sale.Status.WRITTEN_OFF,
)


def confirmed_sales(queryset):
    """Sales counted in volume / business KPIs.

    Excludes cancelled, awaiting-approval, and written-off (uncollectible
    customer debt) sales.
    """
    return queryset.exclude(status__in=EXCLUDED_AGGREGATE_STATUSES)


# Statuses whose ``loss_snapshot`` should appear in the loss totals.
# Includes write-offs (uncollectible debt converted into loss) on top of
# regular pending/paid rows. Cancelled and awaiting rows are excluded
# because their costs were either never realised or were refunded back
# to the supplier.
LOSS_INCLUDED_STATUSES = (
    Sale.Status.PENDING,
    Sale.Status.PAID,
    Sale.Status.WRITTEN_OFF,
)


def loss_eligible_sales(queryset):
    """Restrict to sales whose loss_snapshot counts in loss reports."""
    return queryset.filter(status__in=LOSS_INCLUDED_STATUSES)


def paid_sales_only(queryset):
    """
    Restrict to sales with confirmed payment.

    Profit (``profit_snapshot``) must only be aggregated for **paid** sales.
    Pending sales are recorded but not treated as realized profit until marked paid.
    """
    return queryset.filter(status=Sale.Status.PAID)


def pending_cash_sales(request, get=None):
    """Pending sales the till still expects, with the pending-payments filters.

    ``get`` defaults to ``request.GET``. Pass a ``QueryDict`` when a bulk
    action must re-apply the filters from a posted ``next_query``.
    """
    from django.db.models import Q

    from sales.forms import PendingPaymentsFilterForm
    from sales.models import Sale

    get = request.GET if get is None else get
    original = request.GET
    request.GET = get
    try:
        qs = Sale.objects.filter(status=Sale.Status.PENDING, on_account=False).select_related(
            "company",
            "product",
            "product__line",
            "payment_method",
            "created_by",
            "employee_recipient",
            "employee_recipient__user",
            "employee_recipient__user__profile",
        )
        form = PendingPaymentsFilterForm(get or None)
        data = form.cleaned_data if form.is_valid() else {}
        qs = apply_management_sale_filter_data(qs, data, omit_status=True)
        q = (get.get("q") or "").strip()
        if q:
            qs = qs.filter(
                Q(reference_number__icontains=q)
                | Q(payer_name__icontains=q)
                | Q(product__line__name__icontains=q)
                | Q(company__name__icontains=q)
            )
        return apply_sale_list_ordering(request, qs)
    finally:
        request.GET = original


def apply_management_sale_filter_data(qs, data: Optional[Dict[str, Any]], *, omit_status: bool = False):
    """Apply ManagementSaleFilterForm-style filters to a Sale queryset."""
    if not data:
        return qs
    if data.get("company"):
        qs = qs.filter(company=data["company"])
    if data.get("product"):
        qs = qs.filter(product=data["product"])
    if data.get("employee"):
        qs = qs.filter(created_by=data["employee"])
    if data.get("payment_method"):
        qs = qs.filter(payment_method=data["payment_method"])
    if not omit_status and data.get("status"):
        qs = qs.filter(status=data["status"])
    if data.get("date_from"):
        qs = qs.filter(created_at__date__gte=data["date_from"])
    if data.get("date_to"):
        qs = qs.filter(created_at__date__lte=data["date_to"])
    esim = data.get("esim")
    if esim == "yes":
        qs = qs.filter(is_esim=True)
    elif esim == "no":
        qs = qs.filter(is_esim=False)
    if data.get("entry_mode"):
        qs = qs.filter(entry_mode=data["entry_mode"])
    charge_status = data.get("charge_status")
    if charge_status == NEEDS_VERIFICATION:
        qs = qs.filter(needs_charge_verification_q())
    elif charge_status:
        qs = qs.filter(charge_status=charge_status)
    return qs


SALE_SORT_WHITELIST = {
    "created_at": "created_at",
    "sell": "sell_price_actual",
    "profit": "profit_snapshot",
    "status": "status",
    "ref": "reference_number",
    "payer": "payer_name",
    "company": "company__name",
    "product": "product__line__name",
    "employee": "created_by__username",
}


def apply_sale_list_ordering(request, queryset, *, sort_param="sort", order_param="order"):
    sort = (request.GET.get(sort_param) or "created_at").strip()
    order = (request.GET.get(order_param) or "desc").lower()
    field = SALE_SORT_WHITELIST.get(sort, "created_at")
    if order not in ("asc", "desc"):
        order = "desc"
    prefix = "" if order == "asc" else "-"
    return queryset.order_by(f"{prefix}{field}", f"{prefix}pk")
