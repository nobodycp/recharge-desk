"""Fetch-ready Sky balance rows reconciled against Recharge Desk sales."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from django.utils.translation import gettext_lazy as _

from companies.layan_reconcile import (
    OrphanRefundDetail,
    PhoneActivityBreakdown,
    PhoneActivityDetail,
    SettlementCycleDetail,
    UnpairedRechargeDetail,
    _phone_activity_breakdown,
    _rd_ledger_totals,
    _rd_sales_by_phone,
    norm_phone,
)

REFUND_OP_MARKERS = (
    "إعادة",
    "اعادة",
    "إعاده",
    "فصل",
    "قطع",
    "refund",
    "disconnect",
    "cancel",
)
CHARGE_OP_MARKERS = ("شحن", "طلب جديد", "charge", "topup", "top-up")


def company_supports_sky_reconcile(company) -> bool:
    if (company.phone_refresh_provider or "").lower() == "sky":
        return True
    return "sky" in (company.name or "").lower() or "سكاي" in (company.name or "")


def _dec(value: Any) -> Decimal | None:
    if value in (None, "", "-", "NA", "N/A"):
        return None
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if hasattr(value, "year") and hasattr(value, "hour"):
        return value
    s = str(value).strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _is_refund_op(op: str, delta: Decimal | None) -> bool:
    op_l = (op or "").lower()
    if any(m.lower() in op_l or m in (op or "") for m in REFUND_OP_MARKERS):
        return True
    return delta is not None and delta > 0


def _is_charge_op(op: str, delta: Decimal | None) -> bool:
    if _is_refund_op(op, delta):
        return False
    op_l = (op or "").lower()
    if any(m.lower() in op_l or m in (op or "") for m in CHARGE_OP_MARKERS):
        return True
    return delta is not None and delta < 0


@dataclass
class BalanceAnomaly:
    phone: str
    at: datetime | None
    op: str
    before: Decimal | None
    delta: Decimal | None
    after: Decimal | None
    note: str


@dataclass
class SkyPhoneAgg:
    raw: str
    phone: str
    charges: Decimal = Decimal("0")
    refunds: Decimal = Decimal("0")
    lines: list = field(default_factory=list)

    @property
    def net(self) -> Decimal:
        return self.charges + self.refunds


@dataclass
class SkyReconcilePhoneRow:
    raw: str
    phone: str
    sky_net: Decimal
    sky_charges: Decimal
    sky_refunds: Decimal
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

    # Aliases so shared-style templates can read provider columns uniformly.
    @property
    def layan_net(self) -> Decimal:
        return self.sky_net

    @property
    def layan_charges(self) -> Decimal:
        return self.sky_charges

    @property
    def layan_refunds(self) -> Decimal:
        return self.sky_refunds

    @property
    def gap_abs(self) -> Decimal:
        return abs(self.gap)


@dataclass
class SkyReconcileResult:
    period_from: date | None
    period_to: date | None
    sky_end_balance: Decimal | None
    rd_balance_end: Decimal
    not_recorded: list[SkyReconcilePhoneRow]
    split_settlements: list[SkyReconcilePhoneRow]
    amount_mismatches: list[SkyReconcilePhoneRow]
    matched: list[SkyReconcilePhoneRow]
    rd_only: list[SkyReconcilePhoneRow]
    logged_other_supplier: list[SkyReconcilePhoneRow]
    balance_anomalies: list[BalanceAnomaly]
    total_not_recorded: Decimal
    total_split_settlements: Decimal
    total_amount_mismatch: Decimal
    total_rd_only: Decimal
    total_logged_other_supplier: Decimal
    estimated_deficit: Decimal
    row_count: int
    balance_gap: Decimal
    min_amount_diff: Decimal

    @property
    def action_rows(self) -> list[SkyReconcilePhoneRow]:
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
        return len(self.split_settlements) + len(self.balance_anomalies)


class _SkyLineBag:
    """Duck-type compatible with LayanPhoneAgg for ``_phone_activity_breakdown``."""

    def __init__(self, lines: list):
        self.lines = lines
        self.charges = sum((ln[1] for ln in lines if ln[1] > 0), Decimal("0"))
        self.refunds = sum((ln[1] for ln in lines if ln[1] < 0), Decimal("0"))


def parse_sky_balance_rows(
    rows: list[dict[str, Any]],
    *,
    period_from: date | None,
    period_to: date | None,
) -> tuple[dict[str, SkyPhoneAgg], int, list[BalanceAnomaly], Decimal | None]:
    """Parse Sky template-22 rows into per-phone aggregates.

    Line tuple: ``(datetime, signed_cost, op_label, affects_balance)`` where
    charges are positive costs and refunds are negative (Layan convention),
    derived from Sky signed delta (negative delta ⇒ charge).
    """
    phones: dict[str, SkyPhoneAgg] = {}
    anomalies: list[BalanceAnomaly] = []
    row_count = 0
    end_balance: Decimal | None = None
    end_at: datetime | None = None

    for row in rows or []:
        raw_phone = str(row.get("ext_return_value_6") or "").strip()
        phone = norm_phone(raw_phone)
        if not phone:
            continue

        dt = _parse_dt(row.get("ext_return_value_1"))
        if period_from and dt and dt.date() < period_from:
            continue
        if period_to and dt and dt.date() > period_to:
            continue

        op = str(row.get("ext_return_value_3") or "").strip()
        note = str(row.get("ext_return_value_2") or "").strip()
        before = _dec(row.get("ext_return_value_11"))
        delta = _dec(row.get("ext_return_value_12"))
        after = _dec(row.get("ext_return_value_13"))
        abs_cost = _dec(row.get("ext_return_value_14"))

        if before is not None and delta is not None and after is not None:
            expected = before + delta
            if abs(expected - after) > Decimal("0.01"):
                anomalies.append(
                    BalanceAnomaly(
                        phone=phone,
                        at=dt,
                        op=op,
                        before=before,
                        delta=delta,
                        after=after,
                        note=str(_("before + delta ≠ after")),
                    )
                )

        affects = True
        if before is not None and after is not None and before == after:
            affects = False

        # Signed cost in Layan convention: charge > 0, refund < 0.
        signed_cost = Decimal("0")
        if delta is not None:
            signed_cost = -delta
        elif abs_cost is not None:
            if _is_refund_op(op, delta):
                signed_cost = -abs(abs_cost)
            elif _is_charge_op(op, delta):
                signed_cost = abs(abs_cost)
            else:
                continue
        else:
            continue

        if not affects and signed_cost != 0:
            # Still track for presence / eSIM notes, but breakdown ignores non-moving.
            pass

        op_label = op
        if note:
            op_label = f"{op} ({note})" if op else note

        row_count += 1
        agg = phones.get(phone)
        if not agg:
            agg = SkyPhoneAgg(raw=raw_phone or phone, phone=phone)
            phones[phone] = agg

        if affects:
            if signed_cost > 0:
                agg.charges += signed_cost
            elif signed_cost < 0:
                agg.refunds += signed_cost

        agg.lines.append((dt or datetime.min, signed_cost, op_label, affects))

        if after is not None and dt is not None:
            if end_at is None or dt >= end_at:
                end_at = dt
                end_balance = after

    return phones, row_count, anomalies, end_balance


def _presence_amount(
    sky: SkyPhoneAgg,
    breakdown: PhoneActivityBreakdown,
    sky_match: Decimal,
) -> Decimal:
    if sky_match > 0:
        return sky_match
    if breakdown.had_settlement and breakdown.settlement_net != 0:
        return abs(breakdown.settlement_net)
    positives = sum((ln[1] for ln in sky.lines if ln[1] > 0), Decimal("0"))
    if positives > 0:
        return positives
    if sky.refunds < 0:
        return abs(sky.refunds)
    return Decimal("0")


def explain_sky_row(
    *,
    category: str,
    sky_net: Decimal,
    rd_net: Decimal,
    gap: Decimal,
    other_suppliers: str = "",
) -> str:
    """Short operator-facing reason for why the row is listed."""
    if category == "not_recorded":
        return str(
            _("In Sky for %(amount)s — no matching sale in the system for this period.")
            % {"amount": f"{sky_net:.2f}"}
        )
    if category == "rd_only":
        return str(
            _("In the system for %(amount)s — not found on the Sky balance report.")
            % {"amount": f"{rd_net:.2f}"}
        )
    if category == "other_supplier":
        return str(
            _("Charged on Sky (%(sky)s) but logged under %(suppliers)s.")
            % {"sky": f"{sky_net:.2f}", "suppliers": other_suppliers or "—"}
        )
    if category == "settlement":
        return str(
            _(
                "Charge + disconnect settlement on Sky. Informational only — "
                "not included in estimated deficit."
            )
        )
    if category == "amount_mismatch":
        if gap > 0:
            base = str(
                _("Sky cost %(sky)s is higher than system %(rd)s by %(gap)s.")
                % {
                    "sky": f"{sky_net:.2f}",
                    "rd": f"{rd_net:.2f}",
                    "gap": f"{gap:.2f}",
                }
            )
        else:
            base = str(
                _("System cost %(rd)s is higher than Sky %(sky)s by %(gap)s.")
                % {
                    "sky": f"{sky_net:.2f}",
                    "rd": f"{rd_net:.2f}",
                    "gap": f"{abs(gap):.2f}",
                }
            )
        if abs(gap) == Decimal("5") or abs(gap) == Decimal("5.00"):
            base += " " + str(
                _("Often eSIM fee or package cost mismatch — compare lines below.")
            )
        return base
    if category == "matched":
        return str(_("Sky and system costs match within the minimum difference."))
    return ""


def _rd_sales_by_phone_details(
    company,
    period_from: date | None,
    period_to: date | None,
) -> dict[str, list[dict[str, Any]]]:
    """All non-cancelled company sales in period, keyed by normalized phone."""
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
    out: dict[str, list[dict[str, Any]]] = {}
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


def _annotate_row(
    row: SkyReconcilePhoneRow,
    *,
    rd_sales_map: dict[str, list[dict[str, Any]]],
) -> SkyReconcilePhoneRow:
    row.reason = explain_sky_row(
        category=row.category,
        sky_net=row.sky_net,
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


def reconcile_sky_report(
    company,
    rows: list[dict[str, Any]],
    *,
    period_from: date | None,
    period_to: date | None,
    min_amount_diff: Decimal | None = None,
) -> SkyReconcileResult:
    """Match Sky balance-report rows to RD sales for one Sky supplier."""
    min_diff = Decimal(min_amount_diff if min_amount_diff is not None else 0)
    mismatch_threshold = min_diff if min_diff > 0 else Decimal("0.01")

    sky_phones, row_count, anomalies, sky_end = parse_sky_balance_rows(
        rows, period_from=period_from, period_to=period_to
    )

    rd_by_phone, _rd_suppliers = _rd_sales_by_phone(
        period_from, period_to, company=company
    )
    rd_other_by_phone, rd_other_suppliers = _rd_sales_by_phone(period_from, period_to)
    _rd_dep, _rd_ded, _rd_adj = _rd_ledger_totals(company, period_from, period_to)

    def _other_supplier_note(ph: str) -> str:
        names = [
            n
            for n in (rd_other_suppliers.get(ph) or [])
            if n and n != company.name
        ]
        return ", ".join(names)

    not_recorded: list[SkyReconcilePhoneRow] = []
    split_settlements: list[SkyReconcilePhoneRow] = []
    amount_mismatches: list[SkyReconcilePhoneRow] = []
    matched: list[SkyReconcilePhoneRow] = []
    rd_only: list[SkyReconcilePhoneRow] = []
    logged_other_supplier: list[SkyReconcilePhoneRow] = []

    all_phones = set(sky_phones) | set(rd_by_phone)

    for ph in sorted(all_phones):
        sky = sky_phones.get(ph)
        raw = sky.raw if sky else ph
        lines = sky.lines if sky else []
        sky_chg = sky.charges if sky else Decimal("0")
        sky_ref = sky.refunds if sky else Decimal("0")
        sky_net_ph = sky.net if sky else Decimal("0")

        bag = _SkyLineBag(lines) if sky else _SkyLineBag([])
        breakdown = (
            _phone_activity_breakdown(bag)
            if sky
            else PhoneActivityBreakdown(Decimal("0"), Decimal("0"), 0, False)
        )
        sky_match = (
            breakdown.layan_for_match if breakdown.had_settlement else sky_net_ph
        )
        # eSIM / multi-line: sky_match already sums unpaired charges; when no
        # settlement, sky_net_ph is sum of all balance-moving costs.
        rd_net = rd_by_phone.get(ph, Decimal("0"))
        activity_detail = (
            breakdown.to_detail(sky_chg, sky_ref) if sky else None
        )

        if sky is None and rd_net > 0:
            rd_only.append(
                SkyReconcilePhoneRow(
                    raw=raw,
                    phone=ph,
                    sky_net=Decimal("0"),
                    sky_charges=Decimal("0"),
                    sky_refunds=Decimal("0"),
                    rd_net=rd_net,
                    gap=-rd_net,
                    category="rd_only",
                    category_label=str(_("In system, not in Sky")),
                    lines=[],
                    rd_suppliers=company.name,
                )
            )
            continue

        if sky is None:
            continue

        if breakdown.had_settlement:
            split_settlements.append(
                SkyReconcilePhoneRow(
                    raw=raw,
                    phone=ph,
                    sky_net=breakdown.settlement_net,
                    sky_charges=sky_chg,
                    sky_refunds=sky_ref,
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
            if breakdown.recharge_total <= 0 and rd_net <= 0:
                continue
            if breakdown.recharge_total > 0:
                sky_match = breakdown.recharge_total

        presence_amount = _presence_amount(sky, breakdown, sky_match)
        has_sky_signal = sky_match > 0 or presence_amount > 0 or bool(lines)

        if rd_net <= 0 and has_sky_signal:
            # Pure settlement already listed; skip not_recorded when no leftover.
            if breakdown.had_settlement and breakdown.recharge_total <= 0:
                continue
            display_net = sky_match if sky_match > 0 else presence_amount
            other_note = _other_supplier_note(ph)
            other_net = rd_other_by_phone.get(ph, Decimal("0"))
            if other_note and other_net > Decimal("0"):
                logged_other_supplier.append(
                    SkyReconcilePhoneRow(
                        raw=raw,
                        phone=ph,
                        sky_net=display_net,
                        sky_charges=sky_chg,
                        sky_refunds=sky_ref,
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
                SkyReconcilePhoneRow(
                    raw=raw,
                    phone=ph,
                    sky_net=display_net,
                    sky_charges=sky_chg,
                    sky_refunds=sky_ref,
                    rd_net=Decimal("0"),
                    gap=display_net,
                    category="not_recorded",
                    category_label=str(_("In Sky, not in system")),
                    lines=lines,
                    rd_suppliers=company.name,
                    settlement_amount=breakdown.settlement_net,
                    recharge_amount=breakdown.recharge_total,
                    settlement_cycles=breakdown.settlement_cycles,
                    activity_detail=activity_detail,
                )
            )
            continue

        if sky_match <= 0 and rd_net > 0:
            matched.append(
                SkyReconcilePhoneRow(
                    raw=raw,
                    phone=ph,
                    sky_net=presence_amount,
                    sky_charges=sky_chg,
                    sky_refunds=sky_ref,
                    rd_net=rd_net,
                    gap=presence_amount - rd_net,
                    category="matched",
                    category_label=str(_("Matched")),
                    lines=lines,
                    rd_suppliers=company.name,
                    activity_detail=activity_detail,
                )
            )
            continue

        gap = sky_match - rd_net
        if abs(gap) > mismatch_threshold:
            amount_mismatches.append(
                SkyReconcilePhoneRow(
                    raw=raw,
                    phone=ph,
                    sky_net=sky_match,
                    sky_charges=sky_chg,
                    sky_refunds=sky_ref,
                    rd_net=rd_net,
                    gap=gap,
                    category="amount_mismatch",
                    category_label=str(_("Amount mismatch")),
                    lines=lines,
                    rd_suppliers=company.name,
                    settlement_amount=breakdown.settlement_net,
                    recharge_amount=breakdown.recharge_total,
                    settlement_cycles=breakdown.settlement_cycles,
                    activity_detail=activity_detail,
                )
            )
        else:
            matched.append(
                SkyReconcilePhoneRow(
                    raw=raw,
                    phone=ph,
                    sky_net=sky_match,
                    sky_charges=sky_chg,
                    sky_refunds=sky_ref,
                    rd_net=rd_net,
                    gap=gap,
                    category="matched",
                    category_label=str(_("Matched")),
                    lines=lines,
                    rd_suppliers=company.name,
                    activity_detail=activity_detail,
                )
            )

    total_not = sum((r.gap for r in not_recorded), Decimal("0"))
    total_split = sum((r.settlement_amount for r in split_settlements), Decimal("0"))
    total_mismatch = sum((abs(r.gap) for r in amount_mismatches), Decimal("0"))
    total_rd_only = sum((r.rd_net for r in rd_only), Decimal("0"))
    total_other = sum((r.sky_net for r in logged_other_supplier), Decimal("0"))

    # Actionable Sky-side deficit only (exclude settlements per product decision).
    deficit_mismatch = sum(
        (r.gap for r in amount_mismatches if r.gap > 0),
        Decimal("0"),
    )
    estimated_deficit = total_not + deficit_mismatch

    balance_gap = Decimal("0")
    if sky_end is not None:
        balance_gap = company.current_balance - sky_end

    def _finish(rows: list[SkyReconcilePhoneRow]) -> list[SkyReconcilePhoneRow]:
        return [_annotate_row(r, rd_sales_map=rd_sales_map) for r in rows]

    rd_sales_map = _rd_sales_by_phone_details(company, period_from, period_to)

    return SkyReconcileResult(
        period_from=period_from,
        period_to=period_to,
        sky_end_balance=sky_end,
        rd_balance_end=company.current_balance,
        not_recorded=_finish(not_recorded),
        split_settlements=_finish(split_settlements),
        amount_mismatches=_finish(amount_mismatches),
        matched=_finish(matched),
        rd_only=_finish(rd_only),
        logged_other_supplier=_finish(logged_other_supplier),
        balance_anomalies=anomalies,
        total_not_recorded=total_not,
        total_split_settlements=total_split,
        total_amount_mismatch=total_mismatch,
        total_rd_only=total_rd_only,
        total_logged_other_supplier=total_other,
        estimated_deficit=estimated_deficit,
        row_count=row_count,
        balance_gap=balance_gap,
        min_amount_diff=min_diff,
    )


def fetch_sky_rows_for_reconcile(date_from: date, date_to: date) -> list[dict[str, Any]]:
    """Live pull of template 22 using env Sky credentials."""
    from phone_refresh.providers.sky_sales_client import client_from_env

    client = client_from_env()
    return client.fetch_balance_report(date_from, date_to)
