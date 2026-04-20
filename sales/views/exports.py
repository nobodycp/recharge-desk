"""CSV downloads for sales lists.

Each export reuses the exact same queryset/filter helpers as the HTML
list it parallels, so the user gets a CSV whose rows match what they're
currently looking at — including reference number, payer name, employee
and date filters from ``ManagementSaleFilterForm``.
"""

from __future__ import annotations

from django.db.models import Q
from django.utils.translation import gettext as _g

from accounts.permissions import management_required
from core.csv_export import csv_response, fmt_dt, yesno
from sales.forms import ManagementSaleFilterForm
from sales.models import Sale
from sales.query_utils import (
    apply_management_sale_filter_data,
    apply_sale_list_ordering,
)


def _filtered_sales(request, *, base_qs, omit_status: bool = False):
    """Apply the ManagementSaleFilterForm + free-text ``q`` to ``base_qs``.

    Mirrors the logic in ``management_sale_list`` /
    ``pending_payments`` so the CSV always matches the visible page.
    """
    form = ManagementSaleFilterForm(request.GET or None)
    data = form.cleaned_data if form.is_valid() else {}
    qs = apply_management_sale_filter_data(base_qs, data, omit_status=omit_status)
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(reference_number__icontains=q)
            | Q(payer_name__icontains=q)
            | Q(product__line__name__icontains=q)
            | Q(company__name__icontains=q)
        )
    return apply_sale_list_ordering(request, qs)


def _sale_rows(qs):
    """Generator yielding one CSV tuple per sale.

    ``iterator()`` keeps memory flat for large exports; the
    ``select_related`` on the caller's queryset means we still avoid the
    N+1 cliff while doing so.
    """
    for s in qs.iterator(chunk_size=500):
        yield (
            s.id,
            fmt_dt(s.created_at),
            s.reference_number,
            s.payer_name or "",
            getattr(s.company, "name", ""),
            getattr(getattr(s.product, "line", None), "name", ""),
            getattr(s.product, "variant_label", ""),
            str(s.sell_price_actual),
            str(s.cost_price_snapshot),
            str(s.profit_snapshot),
            str(s.loss_snapshot),
            getattr(s.payment_method, "name", ""),
            s.get_status_display(),
            yesno(s.on_account),
            yesno(s.is_esim),
            getattr(s.customer, "name", "") if s.customer_id else "",
            getattr(s.created_by, "username", ""),
            (s.notes or "").replace("\r\n", " ").replace("\n", " "),
        )


def _sale_headers():
    return [
        _g("ID"),
        _g("Created at"),
        _g("Reference number"),
        _g("Payer name"),
        _g("Company"),
        _g("Product line"),
        _g("Package"),
        _g("Selling price"),
        _g("Cost price"),
        _g("Profit"),
        _g("Loss"),
        _g("Payment method"),
        _g("Status"),
        _g("On account"),
        _g("eSIM"),
        _g("Customer"),
        _g("Employee"),
        _g("Notes"),
    ]


def _base_sales_qs():
    return (
        Sale.objects.select_related(
            "company",
            "product",
            "product__line",
            "payment_method",
            "created_by",
            "customer",
        )
        .order_by("-created_at")
    )


@management_required
def sales_export_csv(request):
    qs = _filtered_sales(request, base_qs=_base_sales_qs())
    return csv_response("sales", _sale_headers(), _sale_rows(qs))


@management_required
def pending_payments_export_csv(request):
    base = _base_sales_qs().filter(status=Sale.Status.PENDING, on_account=False)
    qs = _filtered_sales(request, base_qs=base, omit_status=True)
    return csv_response("pending-payments", _sale_headers(), _sale_rows(qs))


@management_required
def awaiting_approvals_export_csv(request):
    qs = _base_sales_qs().filter(status=Sale.Status.AWAITING)
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(reference_number__icontains=q)
            | Q(payer_name__icontains=q)
            | Q(customer__name__icontains=q)
            | Q(company__name__icontains=q)
        )
    return csv_response("awaiting-approvals", _sale_headers(), _sale_rows(qs))
