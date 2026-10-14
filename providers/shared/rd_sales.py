"""Our own sales / ledger queries used when reconciling against a provider."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from providers.shared.phones import norm_phone


def rd_sales_by_phone(
    period_from: date | None,
    period_to: date | None,
    *,
    company=None,
) -> tuple[dict[str, Decimal], dict[str, list[str]]]:
    """Sum cost snapshots by normalized phone for sales in the period.

    Pass ``company`` to scope matching to one supplier (e.g. Layan only).
    """
    from sales.models import Sale

    qs = Sale.objects.exclude(status=Sale.Status.CANCELLED).select_related("company")
    if company is not None:
        qs = qs.filter(company=company)
    if period_from:
        qs = qs.filter(created_at__date__gte=period_from)
    if period_to:
        qs = qs.filter(created_at__date__lte=period_to)
    out: dict[str, Decimal] = {}
    suppliers: dict[str, list[str]] = {}
    for ref, cost, company_name in qs.values_list(
        "reference_number", "cost_price_snapshot", "company__name"
    ):
        ph = norm_phone(ref or "")
        if not ph:
            continue
        out[ph] = out.get(ph, Decimal("0")) + Decimal(cost)
        names = suppliers.setdefault(ph, [])
        if company_name and company_name not in names:
            names.append(company_name)
    return out, suppliers


def rd_ledger_totals(company, period_from: date | None, period_to: date | None):
    from sales.models import CompanyBalanceTransaction

    qs = company.balance_transactions.all()
    if period_from:
        qs = qs.filter(created_at__date__gte=period_from)
    if period_to:
        qs = qs.filter(created_at__date__lte=period_to)

    deposits = Decimal("0")
    deductions = Decimal("0")
    adjustments = Decimal("0")
    for entry_type, amount in qs.values_list("entry_type", "amount"):
        amount = Decimal(amount)
        if entry_type == CompanyBalanceTransaction.EntryType.DEPOSIT:
            deposits += amount
        elif entry_type == CompanyBalanceTransaction.EntryType.DEDUCTION:
            deductions += amount
        elif entry_type == CompanyBalanceTransaction.EntryType.ADJUSTMENT:
            adjustments += amount
    return deposits, deductions, adjustments
