"""Shared queryset helpers for sale listings."""

from typing import Any, Dict, Optional

from sales.models import Sale


def paid_sales_only(queryset):
    """
    Restrict to sales with confirmed payment.

    Profit (``profit_snapshot``) must only be aggregated for **paid** sales.
    Pending sales are recorded but not treated as realized profit until marked paid.
    """
    return queryset.filter(status=Sale.Status.PAID)


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
    return qs


SALE_SORT_WHITELIST = {
    "created_at": "created_at",
    "sell": "sell_price_actual",
    "profit": "profit_snapshot",
    "status": "status",
    "ref": "reference_number",
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
