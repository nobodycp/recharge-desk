"""Parse Layan charge reports and reconcile against Recharge Desk."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import BinaryIO

from django.utils.translation import gettext_lazy as _

REFUND_MARKERS = ("إعادة مال",)
DEPOSIT_OP = "دفع جديد"


def norm_phone(raw: str) -> str:
    d = re.sub(r"\D", "", str(raw or "").strip())
    if not d:
        return ""
    if d.startswith("972"):
        d = "0" + d[3:]
    if len(d) == 9 and d[0] == "5":
        d = "0" + d
    return d


def _parse_dt(value) -> datetime | None:
    if hasattr(value, "year"):
        return value
    s = str(value or "").strip()
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


@dataclass
class LayanPhoneAgg:
    raw: str
    phone: str
    charges: Decimal = Decimal("0")
    refunds: Decimal = Decimal("0")
    lines: list = field(default_factory=list)

    @property
    def net(self) -> Decimal:
        return self.charges + self.refunds

    @property
    def has_refund(self) -> bool:
        return self.refunds < 0


def _is_disconnect_settlement(lay: LayanPhoneAgg | None) -> bool:
    """Activation charge on Layan plus a refund (disconnect settlement).

    These are tracked separately from amount mismatches; the user may enter
    a manual settlement total (e.g. 100 ₪) outside this report.
    """
    if not lay:
        return False
    return lay.has_refund and lay.charges >= Decimal("20")


@dataclass
class ReconcilePhoneRow:
    raw: str
    phone: str
    layan_net: Decimal
    layan_charges: Decimal
    layan_refunds: Decimal
    rd_net: Decimal
    gap: Decimal
    category: str
    category_label: str
    lines: list
    rd_suppliers: str = ""


@dataclass
class LayanReconcileResult:
    period_from: date | None
    period_to: date | None
    layan_end_balance: Decimal | None
    layan_deposits: Decimal
    layan_net_movement: Decimal
    rd_deposits: Decimal
    rd_deductions: Decimal
    rd_adjustments: Decimal
    rd_balance_end: Decimal
    not_recorded: list[ReconcilePhoneRow]
    split_settlements: list[ReconcilePhoneRow]
    amount_mismatches: list[ReconcilePhoneRow]
    matched: list[ReconcilePhoneRow]
    rd_only: list[ReconcilePhoneRow]
    logged_other_supplier: list[ReconcilePhoneRow]
    total_not_recorded: Decimal
    total_split_settlements: Decimal
    total_amount_mismatch: Decimal
    total_rd_only: Decimal
    total_logged_other_supplier: Decimal
    estimated_deficit: Decimal
    balance_gap: Decimal
    row_count: int


def parse_layan_workbook(
    file_obj: BinaryIO,
    *,
    period_from: date | None,
    period_to: date | None,
) -> tuple[dict[str, LayanPhoneAgg], Decimal, Decimal, Decimal | None, int]:
    try:
        import openpyxl
    except ImportError as exc:
        raise ImportError("openpyxl is required for Layan report import") from exc

    wb = openpyxl.load_workbook(file_obj, read_only=True, data_only=True)
    sheet = wb.active
    by_phone: dict[str, LayanPhoneAgg] = {}
    deposits = Decimal("0")
    net_movement = Decimal("0")
    report_end_balance: Decimal | None = None
    report_end_dt: datetime | None = None
    parsed_rows = 0

    for row in sheet.iter_rows(min_row=3, values_only=True):
        if not row or not row[0]:
            continue
        raw = str(row[0]).strip()
        if raw in ("-", ""):
            continue
        op = str(row[3] or "")
        cost = row[10]
        if cost is None or cost == "-":
            continue
        amount = Decimal(str(cost))
        dt = _parse_dt(row[12] if len(row) > 12 else None)
        if not dt:
            continue
        d = dt.date()
        if period_from and d < period_from:
            continue
        if period_to and d > period_to:
            continue
        parsed_rows += 1
        net_movement += amount

        if op == DEPOSIT_OP:
            deposits += amount
            if report_end_dt is None or dt > report_end_dt:
                report_end_dt = dt
                if len(row) > 8 and row[8] is not None:
                    report_end_balance = Decimal(str(row[8]))
            continue

        ph = norm_phone(raw)
        if not ph:
            continue
        agg = by_phone.get(ph)
        if not agg:
            agg = LayanPhoneAgg(raw=raw, phone=ph)
            by_phone[ph] = agg
        if amount > 0:
            agg.charges += amount
        else:
            agg.refunds += amount
        agg.lines.append((d, amount, op[:50]))

        if report_end_dt is None or dt >= report_end_dt:
            report_end_dt = dt
            if len(row) > 8 and row[8] is not None:
                report_end_balance = Decimal(str(row[8]))

    wb.close()
    return by_phone, deposits, net_movement, report_end_balance, parsed_rows


def _rd_sales_by_phone(
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


def _rd_ledger_totals(company, period_from: date | None, period_to: date | None):
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


def reconcile_layan_report(
    company,
    file_obj: BinaryIO,
    *,
    period_from: date | None,
    period_to: date | None,
    pending_credits: dict[str, Decimal] | None = None,
) -> LayanReconcileResult:
    """Match Layan Excel rows to RD sales for one supplier (intended for Layan)."""
    pending_credits = pending_credits or {}
    layan_phones, layan_dep, layan_net, layan_end, row_count = parse_layan_workbook(
        file_obj, period_from=period_from, period_to=period_to
    )

    for ph, credit in pending_credits.items():
        nph = norm_phone(ph)
        if not nph or credit <= 0:
            continue
        agg = layan_phones.get(nph)
        if not agg:
            agg = LayanPhoneAgg(raw=ph, phone=nph)
            layan_phones[nph] = agg
        agg.refunds -= credit
        agg.lines.append((period_to or date.today(), -credit, _("Pending credit (manual)")))

    if layan_end is not None:
        for credit in pending_credits.values():
            layan_end += credit

    rd_by_phone, rd_layan_suppliers = _rd_sales_by_phone(
        period_from, period_to, company=company
    )
    rd_other_by_phone, rd_other_suppliers = _rd_sales_by_phone(period_from, period_to)
    rd_dep, rd_ded, rd_adj = _rd_ledger_totals(company, period_from, period_to)

    def _other_supplier_note(ph: str) -> str:
        names = [
            n
            for n in (rd_other_suppliers.get(ph) or [])
            if n and n != company.name
        ]
        return ", ".join(names)

    not_recorded: list[ReconcilePhoneRow] = []
    split_settlements: list[ReconcilePhoneRow] = []
    amount_mismatches: list[ReconcilePhoneRow] = []
    matched: list[ReconcilePhoneRow] = []
    rd_only: list[ReconcilePhoneRow] = []
    logged_other_supplier: list[ReconcilePhoneRow] = []

    all_phones = set(layan_phones) | set(rd_by_phone)

    for ph in sorted(all_phones):
        lay = layan_phones.get(ph)
        raw = lay.raw if lay else ph
        layan_net_ph = lay.net if lay else Decimal("0")
        layan_chg = lay.charges if lay else Decimal("0")
        layan_ref = lay.refunds if lay else Decimal("0")
        has_refund = lay.has_refund if lay else False
        lines = lay.lines if lay else []
        rd_net = rd_by_phone.get(ph, Decimal("0"))
        gap = layan_net_ph - rd_net

        if lay is None and rd_net > 0:
            rd_only.append(
                ReconcilePhoneRow(
                    raw=raw,
                    phone=ph,
                    layan_net=Decimal("0"),
                    layan_charges=Decimal("0"),
                    layan_refunds=Decimal("0"),
                    rd_net=rd_net,
                    gap=-rd_net,
                    category="rd_only",
                    category_label=str(_("In Layan sales only")),
                    lines=[],
                    rd_suppliers=company.name,
                )
            )
            continue

        if lay is None:
            continue

        if _is_disconnect_settlement(lay):
            split_settlements.append(
                ReconcilePhoneRow(
                    raw=raw,
                    phone=ph,
                    layan_net=layan_net_ph,
                    layan_charges=layan_chg,
                    layan_refunds=layan_ref,
                    rd_net=rd_net,
                    gap=gap,
                    category="split",
                    category_label=str(_("Settlement on disconnected number")),
                    lines=lines,
                    rd_suppliers=company.name,
                )
            )
            continue

        if rd_net == 0 and layan_net_ph > Decimal("0"):
            other_note = _other_supplier_note(ph)
            other_net = rd_other_by_phone.get(ph, Decimal("0")) - rd_net
            if other_note and other_net > Decimal("0"):
                logged_other_supplier.append(
                    ReconcilePhoneRow(
                        raw=raw,
                        phone=ph,
                        layan_net=layan_net_ph,
                        layan_charges=layan_chg,
                        layan_refunds=layan_ref,
                        rd_net=other_net,
                        gap=layan_net_ph - other_net,
                        category="other_supplier",
                        category_label=str(_("Logged under another supplier")),
                        lines=lines,
                        rd_suppliers=other_note,
                    )
                )
                continue
            not_recorded.append(
                ReconcilePhoneRow(
                    raw=raw,
                    phone=ph,
                    layan_net=layan_net_ph,
                    layan_charges=layan_chg,
                    layan_refunds=layan_ref,
                    rd_net=rd_net,
                    gap=gap,
                    category="not_recorded",
                    category_label=str(_("Not recorded under Layan")),
                    lines=lines,
                    rd_suppliers=company.name,
                )
            )
            continue

        if abs(gap) > Decimal("0.01") and rd_net > 0:
            amount_mismatches.append(
                ReconcilePhoneRow(
                    raw=raw,
                    phone=ph,
                    layan_net=layan_net_ph,
                    layan_charges=layan_chg,
                    layan_refunds=layan_ref,
                    rd_net=rd_net,
                    gap=gap,
                    category="mismatch",
                    category_label=str(_("Amount mismatch")),
                    lines=lines,
                    rd_suppliers=company.name,
                )
            )
            continue

        if layan_chg > 0 or rd_net > 0:
            matched.append(
                ReconcilePhoneRow(
                    raw=raw,
                    phone=ph,
                    layan_net=layan_net_ph,
                    layan_charges=layan_chg,
                    layan_refunds=layan_ref,
                    rd_net=rd_net,
                    gap=gap,
                    category="matched",
                    category_label=str(_("Matched")),
                    lines=lines,
                    rd_suppliers=company.name,
                )
            )

    total_not = sum((r.gap for r in not_recorded), Decimal("0"))
    total_split = sum((r.layan_net for r in split_settlements), Decimal("0"))
    total_mismatch = sum((abs(r.gap) for r in amount_mismatches), Decimal("0"))
    total_rd_only = sum((r.rd_net for r in rd_only), Decimal("0"))
    total_other = sum((r.layan_net for r in logged_other_supplier), Decimal("0"))

    balance_gap = Decimal("0")
    if layan_end is not None:
        balance_gap = company.current_balance - layan_end

    estimated_deficit = total_not + total_split

    return LayanReconcileResult(
        period_from=period_from,
        period_to=period_to,
        layan_end_balance=layan_end,
        layan_deposits=layan_dep,
        layan_net_movement=layan_net,
        rd_deposits=rd_dep,
        rd_deductions=rd_ded,
        rd_adjustments=rd_adj,
        rd_balance_end=company.current_balance,
        not_recorded=not_recorded,
        split_settlements=split_settlements,
        amount_mismatches=amount_mismatches,
        matched=matched,
        rd_only=rd_only,
        logged_other_supplier=logged_other_supplier,
        total_not_recorded=total_not,
        total_split_settlements=total_split,
        total_amount_mismatch=total_mismatch,
        total_rd_only=total_rd_only,
        total_logged_other_supplier=total_other,
        estimated_deficit=estimated_deficit,
        row_count=row_count,
        balance_gap=balance_gap,
    )


def company_supports_layan_reconcile(company) -> bool:
    if (company.phone_refresh_provider or "").lower() == "layan":
        return True
    return "layan" in (company.name or "").lower()


def parse_pending_credits(text: str) -> dict[str, Decimal]:
    """Lines: ``phone,amount`` — credits not yet on the exported file."""
    out: dict[str, Decimal] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.replace("\t", ",").split(",") if p.strip()]
        if len(parts) < 2:
            continue
        try:
            out[parts[0]] = Decimal(parts[1])
        except Exception:
            continue
    return out
