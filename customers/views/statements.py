"""Per-customer statement (دفعات / مسحوبات / رصيد) for a date window.

The page intentionally lives outside ``crud.py`` so the rendering /
aggregation logic does not get in the way of the lean detail page.
Both an HTML view (`customer_statement`) and a CSV download
(`customer_statement_csv`) are exposed; the CSV reuses the same
filtering helpers so what you print is what you export.
"""

from __future__ import annotations

from datetime import date as _date
from decimal import Decimal

from django.db.models import Case, Count, F, Sum, When
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.permissions import management_required
from core.csv_export import csv_response, fmt_dt
from customers.models import Customer, CustomerLedger


# ---------------------------------------------------------------- helpers
def _parse_period(request):
    """Parse ``period_from`` / ``period_to`` GET params; default to month."""
    today = timezone.localdate()
    default_from = today.replace(day=1)

    def _parse(s, fallback):
        try:
            return _date.fromisoformat((s or "").strip())
        except (ValueError, TypeError):
            return fallback

    return (
        _parse(request.GET.get("period_from"), default_from),
        _parse(request.GET.get("period_to"), today),
    )


def _signed_amount_expression():
    """SQL ``CASE`` that converts CustomerLedger rows into signed deltas.

    Mirrors :func:`customers.services._apply_balance_delta`:
    CHARGE / ADJUSTMENT raise the balance, PAYMENT / REVERSAL lower it.
    """
    return Case(
        When(
            entry_type__in=[
                CustomerLedger.EntryType.CHARGE,
                CustomerLedger.EntryType.ADJUSTMENT,
            ],
            then=F("amount"),
        ),
        default=-F("amount"),
    )


def _balance_at(customer, when_date):
    """Customer balance immediately before ``when_date`` (00:00 local).

    Computed from the ledger so it always agrees with
    ``current_balance``. ``when_date=None`` returns 0 (the lifetime
    starting point).
    """
    if when_date is None:
        return Decimal("0")
    delta = (
        customer.ledger_entries.filter(created_at__date__lt=when_date)
        .aggregate(s=Sum(_signed_amount_expression()))["s"]
    )
    return Decimal(delta or 0)


def _build_statement_context(customer, period_from, period_to):
    """Return the dict shared by the HTML view and the CSV download."""
    from sales.models import Sale  # local import to avoid app cycle

    ledger_in_range = customer.ledger_entries.filter(
        created_at__date__gte=period_from,
        created_at__date__lte=period_to,
    ).select_related("sale", "sale__company", "sale__product", "payment", "created_by")

    payments_in_range = customer.payments.filter(
        created_at__date__gte=period_from,
        created_at__date__lte=period_to,
    ).select_related("payment_method", "created_by")

    sales_in_range = (
        Sale.objects.filter(
            customer=customer,
            on_account=True,
            created_at__date__gte=period_from,
            created_at__date__lte=period_to,
        )
        .exclude(status=Sale.Status.CANCELLED)
        .select_related("company", "product", "product__line", "payment_method", "created_by")
    )

    # --- KPIs ---
    charges_total = ledger_in_range.filter(
        entry_type=CustomerLedger.EntryType.CHARGE
    ).aggregate(s=Sum("amount"))["s"] or Decimal("0")
    payments_total = payments_in_range.aggregate(s=Sum("amount"))["s"] or Decimal("0")
    adjustments_total = ledger_in_range.filter(
        entry_type=CustomerLedger.EntryType.ADJUSTMENT
    ).aggregate(s=Sum("amount"))["s"] or Decimal("0")
    reversals_total = ledger_in_range.filter(
        entry_type=CustomerLedger.EntryType.REVERSAL
    ).aggregate(s=Sum("amount"))["s"] or Decimal("0")

    opening_balance = _balance_at(customer, period_from)
    period_delta = (
        ledger_in_range.aggregate(s=Sum(_signed_amount_expression()))["s"] or Decimal("0")
    )
    closing_balance = opening_balance + period_delta

    sales_summary = sales_in_range.aggregate(
        cnt=Count("id"),
        total_sell=Sum("sell_price_actual"),
    )

    # --- Per-product breakdown of what they took on account ---
    by_product = list(
        sales_in_range.values(
            "company__name",
            "product__line__name",
            "product__variant_label",
        )
        .annotate(cnt=Count("id"), total_sell=Sum("sell_price_actual"))
        .order_by("-total_sell", "company__name")
    )

    # --- Per-payment-method breakdown of incoming money ---
    by_payment_method = list(
        payments_in_range.values("payment_method__name")
        .annotate(cnt=Count("id"), total=Sum("amount"))
        .order_by("-total")
    )

    # Running balance per ledger entry, oldest -> newest. We attach the
    # value as ``.running_balance`` so the template can render it in a
    # column without doing arithmetic itself (Django templates can't
    # carry mutable state across loop iterations).
    ledger_chrono = list(ledger_in_range.order_by("created_at", "id"))
    running = opening_balance
    for entry in ledger_chrono:
        if entry.entry_type in (
            CustomerLedger.EntryType.CHARGE,
            CustomerLedger.EntryType.ADJUSTMENT,
        ):
            running += Decimal(entry.amount)
        else:
            running -= Decimal(entry.amount)
        entry.running_balance = running

    return {
        "customer": customer,
        "period_from": period_from,
        "period_to": period_to,
        "opening_balance": opening_balance,
        "closing_balance": closing_balance,
        "charges_total": charges_total,
        "payments_total": payments_total,
        "adjustments_total": adjustments_total,
        "reversals_total": reversals_total,
        "net_change": period_delta,
        "sales_summary": sales_summary,
        "by_product": by_product,
        "by_payment_method": by_payment_method,
        "sales_in_range": sales_in_range.order_by("-created_at"),
        "payments_in_range": payments_in_range.order_by("-created_at"),
        "ledger_chrono": ledger_chrono,
    }


# ---------------------------------------------------------------- views
@management_required
def customer_statement(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    period_from, period_to = _parse_period(request)
    ctx = _build_statement_context(customer, period_from, period_to)
    ctx["title"] = _("Customer statement")
    return render(request, "customers/customer_statement.html", ctx)


@management_required
def customer_detailed_invoice(request, pk):
    """Print-first detailed invoice: every ledger row as its own line.

    Unlike :func:`customer_statement`, this view hides the per-product
    and per-method aggregations and focuses on a transaction-by-transaction
    audit trail the customer can reconcile against their own records.
    """
    customer = get_object_or_404(Customer, pk=pk)
    period_from, period_to = _parse_period(request)
    ctx = _build_statement_context(customer, period_from, period_to)
    ctx["title"] = _("Detailed invoice")
    return render(request, "customers/customer_detailed_invoice.html", ctx)


@management_required
def customer_statement_csv(request, pk):
    """Single CSV with ledger rows for the requested period.

    Each row has a ``type`` column so the same file can be sliced by
    Excel filters into "what they took" vs. "what they paid". A second
    "summary" line trails the data with totals so a print preview from
    Excel matches what the HTML page shows.
    """
    customer = get_object_or_404(Customer, pk=pk)
    period_from, period_to = _parse_period(request)
    ctx = _build_statement_context(customer, period_from, period_to)

    headers = [
        str(_("Date")),
        str(_("Type")),
        str(_("Description")),
        str(_("Reference")),
        str(_("Charge")),
        str(_("Payment")),
        str(_("Notes")),
    ]

    def rows():
        for entry in ctx["ledger_chrono"]:
            etype = entry.get_entry_type_display()
            charge = ""
            payment = ""
            if entry.entry_type == CustomerLedger.EntryType.CHARGE:
                charge = str(entry.amount)
                desc = (
                    f"{entry.sale.company.name if entry.sale and entry.sale.company else ''} "
                    f"— {entry.sale.product.display_name if entry.sale and entry.sale.product else ''}"
                ).strip(" —")
                ref = entry.sale.reference_number if entry.sale else ""
            elif entry.entry_type == CustomerLedger.EntryType.PAYMENT:
                payment = str(entry.amount)
                desc = entry.payment.payment_method.name if entry.payment and entry.payment.payment_method else ""
                ref = ""
            elif entry.entry_type == CustomerLedger.EntryType.ADJUSTMENT:
                if entry.amount >= 0:
                    charge = str(entry.amount)
                else:
                    payment = str(-entry.amount)
                desc = str(_("Adjustment"))
                ref = ""
            else:  # REVERSAL
                payment = str(entry.amount)
                desc = str(_("Reversal"))
                ref = entry.sale.reference_number if entry.sale else ""
            yield [
                fmt_dt(entry.created_at),
                etype,
                desc,
                ref,
                charge,
                payment,
                entry.notes or "",
            ]
        # Summary trailer
        yield []
        yield [str(_("Opening balance")), "", "", "", "", str(ctx["opening_balance"]), ""]
        yield [str(_("Charges total")), "", "", "", str(ctx["charges_total"]), "", ""]
        yield [str(_("Payments total")), "", "", "", "", str(ctx["payments_total"]), ""]
        yield [str(_("Closing balance")), "", "", "", "", str(ctx["closing_balance"]), ""]

    safe_name = "".join(c if c.isalnum() else "_" for c in customer.name)[:40] or "customer"
    stem = f"statement_{customer.pk}_{safe_name}_{period_from}_{period_to}"
    return csv_response(stem, headers, rows())
