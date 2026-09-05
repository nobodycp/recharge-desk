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
BALANCE_BEFORE_COL = 7
BALANCE_AFTER_COL = 8
COST_COL = 10
DATE_COL = 12


def norm_phone(raw: str) -> str:
    d = re.sub(r"\D", "", str(raw or "").strip())
    if not d:
        return ""
    if d.startswith("972"):
        d = "0" + d[3:]
    if len(d) == 9 and d[0] == "5":
        d = "0" + d
    return d


def _row_affects_balance(row) -> bool:
    """True when the portal balance actually moved (before ≠ after).

    Rows like re-activation (``تفعيل خط هاتف جديد``) often show a cost
    column but keep the same balance; those must not enter reconciliation.
    """
    if len(row) <= BALANCE_AFTER_COL:
        return True
    before = row[BALANCE_BEFORE_COL]
    after = row[BALANCE_AFTER_COL]
    if before in (None, "", "-") or after in (None, "", "-"):
        return True
    try:
        return Decimal(str(before)) != Decimal(str(after))
    except Exception:
        return True


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


@dataclass
class SettlementCycleDetail:
    charge_amount: Decimal
    refund_amount: Decimal
    retained: Decimal
    charge_at: datetime
    refund_at: datetime
    charge_op: str
    refund_op: str


@dataclass
class OrphanRefundDetail:
    amount: Decimal
    at: datetime
    op: str


@dataclass
class UnpairedRechargeDetail:
    amount: Decimal
    at: datetime
    op: str


@dataclass
class PhoneActivityDetail:
    """Per-number Layan report breakdown for the reconcile UI."""

    cycles: list[SettlementCycleDetail]
    orphan_refunds: list[OrphanRefundDetail]
    recharges: list[UnpairedRechargeDetail]
    layan_charges: Decimal
    layan_refunds: Decimal

    @property
    def layan_net_movement(self) -> Decimal:
        return self.layan_charges + self.layan_refunds


@dataclass
class PhoneActivityBreakdown:
    """Charge+refund cycles (settlements) vs standalone recharges on one number."""

    settlement_net: Decimal
    recharge_total: Decimal
    settlement_cycles: int
    had_settlement: bool
    cycles: list[SettlementCycleDetail] = field(default_factory=list)
    orphan_refunds: list[OrphanRefundDetail] = field(default_factory=list)
    recharges: list[UnpairedRechargeDetail] = field(default_factory=list)

    @property
    def layan_for_match(self) -> Decimal:
        return self.recharge_total

    def to_detail(self, charges: Decimal, refunds: Decimal) -> PhoneActivityDetail:
        return PhoneActivityDetail(
            cycles=self.cycles,
            orphan_refunds=self.orphan_refunds,
            recharges=self.recharges,
            layan_charges=charges,
            layan_refunds=refunds,
        )


def _same_day_passive_charges(lay: LayanPhoneAgg) -> Decimal:
    """Re-activation rows (no balance move) on a day that also has a real charge.

    Layan often shows both on the report; only one sale exists in RD.
    """
    balance_days = {
        (ln[0].date() if hasattr(ln[0], "date") else ln[0])
        for ln in lay.lines
        if len(ln) > 3 and ln[3] and ln[1] > 0
    }
    total = Decimal("0")
    for ln in lay.lines:
        if len(ln) < 4 or ln[3] or ln[1] <= 0:
            continue
        d = ln[0].date() if hasattr(ln[0], "date") else ln[0]
        if d in balance_days:
            total += ln[1]
    return total


def _all_positive_costs(lay: LayanPhoneAgg) -> Decimal:
    """Sum of every positive cost line (balance-moving and passive)."""
    return sum(
        (ln[1] for ln in lay.lines if len(ln) > 1 and ln[1] > 0),
        Decimal("0"),
    )


def _layan_presence_amount(
    lay: LayanPhoneAgg,
    breakdown: PhoneActivityBreakdown,
    layan_match: Decimal,
) -> Decimal:
    """Amount shown when listing a Layan-only number (presence, not only net)."""
    if layan_match > 0:
        return layan_match
    if breakdown.had_settlement and breakdown.settlement_net != 0:
        return abs(breakdown.settlement_net)
    positives = _all_positive_costs(lay)
    if positives > 0:
        return positives
    if lay.refunds < 0:
        return abs(lay.refunds)
    return Decimal("0")


def _phone_activity_breakdown(lay: LayanPhoneAgg) -> PhoneActivityBreakdown:
    """Pair each charge with the next refund by time; leftover charges are recharges."""
    ordered = sorted(
        (ln for ln in lay.lines if len(ln) > 3 and ln[3]),
        key=lambda x: x[0],
    )
    settlement_net = Decimal("0")
    recharge_total = Decimal("0")
    used_refunds: set[int] = set()
    cycles = 0
    cycle_details: list[SettlementCycleDetail] = []
    recharge_details: list[UnpairedRechargeDetail] = []
    for i, ln in enumerate(ordered):
        amount = ln[1]
        if amount <= 0:
            continue
        paired = False
        for j in range(i + 1, len(ordered)):
            if j in used_refunds:
                continue
            refund_ln = ordered[j]
            refund_amt = refund_ln[1]
            if refund_amt < 0:
                retained = amount + refund_amt
                settlement_net += retained
                used_refunds.add(j)
                cycles += 1
                cycle_details.append(
                    SettlementCycleDetail(
                        charge_amount=amount,
                        refund_amount=refund_amt,
                        retained=retained,
                        charge_at=ln[0],
                        refund_at=refund_ln[0],
                        charge_op=ln[2],
                        refund_op=refund_ln[2],
                    )
                )
                paired = True
                break
        if not paired:
            recharge_total += amount
            recharge_details.append(
                UnpairedRechargeDetail(amount=amount, at=ln[0], op=ln[2])
            )
    orphan_details: list[OrphanRefundDetail] = []
    for j, ln in enumerate(ordered):
        if ln[1] < 0 and j not in used_refunds:
            orphan_details.append(
                OrphanRefundDetail(amount=ln[1], at=ln[0], op=ln[2])
            )
    return PhoneActivityBreakdown(
        settlement_net=settlement_net,
        recharge_total=recharge_total,
        settlement_cycles=cycles,
        had_settlement=cycles > 0,
        cycles=cycle_details,
        orphan_refunds=orphan_details,
        recharges=recharge_details,
    )


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
    settlement_amount: Decimal = Decimal("0")
    recharge_amount: Decimal = Decimal("0")
    settlement_cycles: int = 0
    activity_detail: PhoneActivityDetail | None = None
    reason: str = ""
    rd_sales: list = field(default_factory=list)

    @property
    def sky_net(self) -> Decimal:
        """Alias for shared review-table templates (provider cost column)."""
        return self.layan_net


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
    min_settlement_difference: Decimal = Decimal("0")
    split_settlements_hidden_count: int = 0

    @property
    def action_rows(self) -> list[ReconcilePhoneRow]:
        return list(self.not_recorded) + list(self.rd_only) + list(self.logged_other_supplier)

    @property
    def action_count(self) -> int:
        return len(self.action_rows)

    @property
    def action_total(self) -> Decimal:
        return (
            self.total_not_recorded
            + self.total_rd_only
            + self.total_logged_other_supplier
        )

    @property
    def mismatch_count(self) -> int:
        return len(self.amount_mismatches)

    @property
    def info_count(self) -> int:
        return len(self.split_settlements)


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
        cost = row[COST_COL] if len(row) > COST_COL else None
        if cost is None or cost == "-":
            continue
        amount = Decimal(str(cost))
        dt = _parse_dt(row[DATE_COL] if len(row) > DATE_COL else None)
        if not dt:
            continue
        d = dt.date()
        if period_from and d < period_from:
            continue
        if period_to and d > period_to:
            continue

        if op == DEPOSIT_OP:
            parsed_rows += 1
            net_movement += amount
            deposits += amount
            if report_end_dt is None or dt > report_end_dt:
                report_end_dt = dt
                if row[BALANCE_AFTER_COL] is not None:
                    report_end_balance = Decimal(str(row[BALANCE_AFTER_COL]))
            continue

        ph = norm_phone(raw)
        if not ph:
            continue
        affects = _row_affects_balance(row)
        agg = by_phone.get(ph)
        if not agg:
            agg = LayanPhoneAgg(raw=raw, phone=ph)
            by_phone[ph] = agg
        agg.lines.append((dt, amount, op[:50], affects))

        if not affects:
            continue

        parsed_rows += 1
        net_movement += amount
        if amount > 0:
            agg.charges += amount
        else:
            agg.refunds += amount

        if report_end_dt is None or dt >= report_end_dt:
            report_end_dt = dt
            if row[BALANCE_AFTER_COL] is not None:
                report_end_balance = Decimal(str(row[BALANCE_AFTER_COL]))

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


def _settlement_row_difference(row: ReconcilePhoneRow) -> Decimal:
    return abs(row.settlement_amount or row.layan_net)


def reconcile_layan_report(
    company,
    file_obj: BinaryIO,
    *,
    period_from: date | None,
    period_to: date | None,
    pending_credits: dict[str, Decimal] | None = None,
    min_settlement_difference: Decimal | None = None,
) -> LayanReconcileResult:
    """Match Layan Excel rows to RD sales for one supplier (intended for Layan).

    Categories (by design):
    1. In Layan, not in system — any Layan number with no Layan-company sale
    2. In system, not in Layan — any Layan-company sale missing from the report
    3. Settlements — charge + disconnect in the period (retained / loss amount)
    4. Sales differences — both sides present and |gap| exceeds the entered minimum
    """
    pending_credits = pending_credits or {}
    min_diff = Decimal(min_settlement_difference or 0)
    # Tiny floor so exact equals still count as matched when min_diff is 0.
    mismatch_threshold = min_diff if min_diff > 0 else Decimal("0.01")

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
        agg.lines.append(
            (period_to or date.today(), -credit, _("Pending credit (manual)"), True)
        )

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
        lines = lay.lines if lay else []
        layan_chg = lay.charges if lay else Decimal("0")
        layan_ref = lay.refunds if lay else Decimal("0")
        layan_net_ph = lay.net if lay else Decimal("0")
        breakdown = (
            _phone_activity_breakdown(lay)
            if lay
            else PhoneActivityBreakdown(Decimal("0"), Decimal("0"), 0, False)
        )
        passive_same_day = _same_day_passive_charges(lay) if lay else Decimal("0")
        base_match = (
            breakdown.layan_for_match
            if breakdown.had_settlement
            else layan_net_ph
        )
        layan_match = base_match + passive_same_day
        rd_net = rd_by_phone.get(ph, Decimal("0"))
        gap = layan_match - rd_net
        activity_detail = (
            breakdown.to_detail(layan_chg, layan_ref) if lay else None
        )

        # --- 2. In system, not in Layan ---
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
                    category_label=str(_("In system, not in Layan")),
                    lines=[],
                    rd_suppliers=company.name,
                )
            )
            continue

        if lay is None:
            continue

        # --- 3. Settlements (charge + disconnect in period) ---
        if breakdown.had_settlement:
            split_settlements.append(
                ReconcilePhoneRow(
                    raw=raw,
                    phone=ph,
                    layan_net=breakdown.settlement_net,
                    layan_charges=layan_chg,
                    layan_refunds=layan_ref,
                    rd_net=Decimal("0"),
                    gap=Decimal("0"),
                    category="settlement",
                    category_label=str(_("Settlement on disconnected number")),
                    lines=lines,
                    rd_suppliers=company.name,
                    settlement_amount=breakdown.settlement_net,
                    recharge_amount=breakdown.recharge_total,
                    settlement_cycles=breakdown.settlement_cycles,
                    activity_detail=activity_detail,
                )
            )
            # Pure settlement: no leftover recharge to treat as a sale row.
            if breakdown.recharge_total <= 0:
                continue

        # Presence amount for listing Layan-only numbers (includes passive /
        # orphan rows that the old net<=0 skip used to hide).
        presence_amount = _layan_presence_amount(lay, breakdown, layan_match)
        has_layan_signal = (
            layan_match > 0
            or presence_amount > 0
            or bool(lines)
        )

        # --- 1. In Layan, not in system ---
        if rd_net <= 0 and has_layan_signal:
            display_net = layan_match if layan_match > 0 else presence_amount
            display_gap = display_net
            other_note = _other_supplier_note(ph)
            other_net = rd_other_by_phone.get(ph, Decimal("0"))
            if other_note and other_net > Decimal("0"):
                logged_other_supplier.append(
                    ReconcilePhoneRow(
                        raw=raw,
                        phone=ph,
                        layan_net=display_net,
                        layan_charges=layan_chg,
                        layan_refunds=layan_ref,
                        rd_net=other_net,
                        gap=display_net - other_net,
                        category="other_supplier",
                        category_label=str(_("Logged under another supplier")),
                        lines=lines,
                        rd_suppliers=other_note,
                        settlement_amount=breakdown.settlement_net,
                        recharge_amount=breakdown.recharge_total,
                        settlement_cycles=breakdown.settlement_cycles,
                        activity_detail=activity_detail,
                    )
                )
                continue
            not_recorded.append(
                ReconcilePhoneRow(
                    raw=raw,
                    phone=ph,
                    layan_net=display_net,
                    layan_charges=layan_chg,
                    layan_refunds=layan_ref,
                    rd_net=Decimal("0"),
                    gap=display_gap,
                    category="not_recorded",
                    category_label=str(_("In Layan, not in system")),
                    lines=lines,
                    rd_suppliers=company.name,
                    settlement_amount=breakdown.settlement_net,
                    recharge_amount=breakdown.recharge_total,
                    settlement_cycles=breakdown.settlement_cycles,
                    activity_detail=activity_detail,
                )
            )
            continue

        # Layan presence without a balance-moving sale amount: both sides have
        # the number, but there is nothing meaningful to diff as a sale.
        if layan_match <= 0 and rd_net > 0:
            matched.append(
                ReconcilePhoneRow(
                    raw=raw,
                    phone=ph,
                    layan_net=presence_amount,
                    layan_charges=layan_chg,
                    layan_refunds=layan_ref,
                    rd_net=rd_net,
                    gap=presence_amount - rd_net,
                    category="matched",
                    category_label=str(_("Matched")),
                    lines=lines,
                    rd_suppliers=company.name,
                    settlement_amount=breakdown.settlement_net,
                    recharge_amount=breakdown.recharge_total,
                    settlement_cycles=breakdown.settlement_cycles,
                    activity_detail=activity_detail,
                )
            )
            continue

        # No sale-side activity left to compare (e.g. settlement handled above).
        if layan_match <= 0 and rd_net <= 0:
            continue

        # --- 4. Sales differences (both sides; gap above entered minimum) ---
        if rd_net > 0 and abs(gap) > mismatch_threshold:
            amount_mismatches.append(
                ReconcilePhoneRow(
                    raw=raw,
                    phone=ph,
                    layan_net=layan_match,
                    layan_charges=layan_chg,
                    layan_refunds=layan_ref,
                    rd_net=rd_net,
                    gap=gap,
                    category="mismatch",
                    category_label=str(_("Sales difference (Layan vs system)")),
                    lines=lines,
                    rd_suppliers=company.name,
                    settlement_amount=breakdown.settlement_net,
                    recharge_amount=breakdown.recharge_total,
                    settlement_cycles=breakdown.settlement_cycles,
                    activity_detail=activity_detail,
                )
            )
            continue

        if layan_match > 0 or rd_net > 0:
            matched.append(
                ReconcilePhoneRow(
                    raw=raw,
                    phone=ph,
                    layan_net=layan_match if layan_match > 0 else presence_amount,
                    layan_charges=layan_chg,
                    layan_refunds=layan_ref,
                    rd_net=rd_net,
                    gap=gap if layan_match > 0 else (presence_amount - rd_net),
                    category="matched",
                    category_label=str(_("Matched")),
                    lines=lines,
                    rd_suppliers=company.name,
                    settlement_amount=breakdown.settlement_net,
                    recharge_amount=breakdown.recharge_total,
                    settlement_cycles=breakdown.settlement_cycles,
                    activity_detail=activity_detail,
                )
            )

    split_settlements.sort(
        key=lambda r: r.settlement_amount or r.layan_net,
        reverse=True,
    )

    split_hidden = 0
    if min_diff > 0:
        visible = [
            r
            for r in split_settlements
            if _settlement_row_difference(r) >= min_diff
        ]
        split_hidden = len(split_settlements) - len(visible)
        split_settlements = visible

    # Deficit: balance-affecting Layan charges missing from RD + settlement retained.
    total_not = sum(
        (
            r.gap
            for r in not_recorded
            if r.layan_charges > 0 or r.recharge_amount > 0
        ),
        Decimal("0"),
    )
    # Presence-only rows (passive / orphan) still listed; count their gap for the
    # section total so the UI total matches the visible column.
    total_not_section = sum((r.gap for r in not_recorded), Decimal("0"))
    total_split = sum((r.settlement_amount for r in split_settlements), Decimal("0"))
    total_mismatch = sum((abs(r.gap) for r in amount_mismatches), Decimal("0"))
    total_rd_only = sum((r.rd_net for r in rd_only), Decimal("0"))
    total_other = sum((r.layan_net for r in logged_other_supplier), Decimal("0"))

    balance_gap = Decimal("0")
    if layan_end is not None:
        balance_gap = company.current_balance - layan_end

    estimated_deficit = total_not + total_split

    rd_sales_map = _layan_rd_sales_by_phone_details(company, period_from, period_to)

    def _finish(rows: list[ReconcilePhoneRow]) -> list[ReconcilePhoneRow]:
        return [_annotate_layan_row(r, rd_sales_map=rd_sales_map) for r in rows]

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
        not_recorded=_finish(not_recorded),
        split_settlements=_finish(split_settlements),
        amount_mismatches=_finish(amount_mismatches),
        matched=_finish(matched),
        rd_only=_finish(rd_only),
        logged_other_supplier=_finish(logged_other_supplier),
        total_not_recorded=total_not_section,
        total_split_settlements=total_split,
        total_amount_mismatch=total_mismatch,
        total_rd_only=total_rd_only,
        total_logged_other_supplier=total_other,
        estimated_deficit=estimated_deficit,
        row_count=row_count,
        balance_gap=balance_gap,
        min_settlement_difference=min_diff,
        split_settlements_hidden_count=split_hidden,
    )


def explain_layan_row(
    *,
    category: str,
    layan_net: Decimal,
    rd_net: Decimal,
    gap: Decimal,
    other_suppliers: str = "",
) -> str:
    if category == "not_recorded":
        return str(
            _("In Layan for %(amount)s — no matching sale in the system for this period.")
            % {"amount": f"{layan_net:.2f}"}
        )
    if category == "rd_only":
        return str(
            _("In the system for %(amount)s — not found on the uploaded Layan report.")
            % {"amount": f"{rd_net:.2f}"}
        )
    if category == "other_supplier":
        return str(
            _("Charged on Layan (%(layan)s) but logged under %(suppliers)s.")
            % {"layan": f"{layan_net:.2f}", "suppliers": other_suppliers or "—"}
        )
    if category == "settlement":
        return str(
            _(
                "Charge + disconnect settlement on Layan. Review retained amount; "
                "it is included in estimated deficit."
            )
        )
    if category == "amount_mismatch":
        if gap > 0:
            return str(
                _("Layan cost %(layan)s is higher than system %(rd)s by %(gap)s.")
                % {
                    "layan": f"{layan_net:.2f}",
                    "rd": f"{rd_net:.2f}",
                    "gap": f"{gap:.2f}",
                }
            )
        return str(
            _("System cost %(rd)s is higher than Layan %(layan)s by %(gap)s.")
            % {
                "layan": f"{layan_net:.2f}",
                "rd": f"{rd_net:.2f}",
                "gap": f"{abs(gap):.2f}",
            }
        )
    if category == "matched":
        return str(_("Layan and system costs match within the minimum difference."))
    return ""


def _layan_rd_sales_by_phone_details(
    company,
    period_from: date | None,
    period_to: date | None,
) -> dict[str, list]:
    from sales.models import Sale

    qs = (
        Sale.objects.exclude(status=Sale.Status.CANCELLED)
        .filter(company=company)
        .select_related("product")
        .order_by("created_at")
    )
    if period_from:
        qs = qs.filter(created_at__date__gte=period_from)
    if period_to:
        qs = qs.filter(created_at__date__lte=period_to)
    out: dict[str, list] = {}
    for sale in qs:
        phone = norm_phone(sale.reference_number or "")
        if not phone:
            continue
        product = sale.product
        label = getattr(product, "variant_label", None) or str(product)
        out.setdefault(phone, []).append(
            {
                "id": sale.pk,
                "at": sale.created_at,
                "cost": Decimal(sale.cost_price_snapshot),
                "product": label,
                "is_esim": bool(sale.is_esim),
                "status": sale.status,
            }
        )
    return out


def _annotate_layan_row(
    row: ReconcilePhoneRow,
    *,
    rd_sales_map: dict[str, list],
) -> ReconcilePhoneRow:
    row.reason = explain_layan_row(
        category=row.category,
        layan_net=row.layan_net,
        rd_net=row.rd_net,
        gap=row.gap,
        other_suppliers=row.rd_suppliers if row.category == "other_supplier" else "",
    )
    if row.category in (
        "amount_mismatch",
        "rd_only",
        "not_recorded",
        "other_supplier",
        "settlement",
    ):
        row.rd_sales = list(rd_sales_map.get(row.phone) or [])
    return row


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
