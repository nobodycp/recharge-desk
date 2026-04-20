"""CSV downloads for customers + customer payments.

Customer rows mirror the customer list page (same ``q`` filter), with
the headline balance metric, total phones recorded, when the row was
created and by whom. Payment rows are aggregated across all customers
with optional date / customer / method filters so management can
spot-check daily collections in Excel.
"""

from __future__ import annotations

from django.db.models import Count, Q
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _g

from accounts.permissions import management_required
from core.csv_export import csv_response, fmt_dt
from customers.models import Customer, CustomerPayment


def _balance_kind(balance) -> str:
    if balance > 0:
        return _g("Debt")
    if balance < 0:
        return _g("Credit")
    return _g("Settled")


@management_required
def customers_export_csv(request):
    qs = (
        Customer.objects.annotate(phones_count=Count("phones", distinct=True))
        .select_related("created_by")
        .order_by("-current_balance", "name")
    )
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(phones__phone__icontains=q)).distinct()

    headers = [
        _g("ID"),
        _g("Name"),
        _g("Balance"),
        _g("Status"),
        _g("Phones"),
        _g("Active"),
        _g("Created at"),
        _g("Created by"),
        _g("Notes"),
    ]

    def rows():
        for c in qs.iterator(chunk_size=500):
            yield (
                c.id,
                c.name,
                str(abs(c.current_balance)),
                _balance_kind(c.current_balance),
                c.phones_count,
                _g("Yes") if c.is_active else _g("No"),
                fmt_dt(c.created_at),
                getattr(c.created_by, "username", ""),
                (c.notes or "").replace("\r\n", " ").replace("\n", " "),
            )

    return csv_response("customers", headers, rows())


@management_required
def customer_payments_export_csv(request):
    qs = CustomerPayment.objects.select_related(
        "customer", "payment_method", "created_by"
    ).order_by("-created_at")

    customer_id = request.GET.get("customer") or ""
    if customer_id.isdigit():
        qs = qs.filter(customer_id=int(customer_id))

    method_id = request.GET.get("payment_method") or ""
    if method_id.isdigit():
        qs = qs.filter(payment_method_id=int(method_id))

    date_from = parse_date(request.GET.get("date_from") or "")
    date_to = parse_date(request.GET.get("date_to") or "")
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)

    headers = [
        _g("ID"),
        _g("Created at"),
        _g("Customer"),
        _g("Amount"),
        _g("Payment method"),
        _g("Recorded by"),
        _g("Notes"),
    ]

    def rows():
        for p in qs.iterator(chunk_size=500):
            yield (
                p.id,
                fmt_dt(p.created_at),
                getattr(p.customer, "name", ""),
                str(p.amount),
                getattr(p.payment_method, "name", ""),
                getattr(p.created_by, "username", ""),
                (p.notes or "").replace("\r\n", " ").replace("\n", " "),
            )

    return csv_response("customer-payments", headers, rows())
