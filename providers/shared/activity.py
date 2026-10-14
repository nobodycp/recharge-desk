"""Per-number charge / refund pairing used by provider reconciliation."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


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


def phone_activity_breakdown(lay: Any) -> PhoneActivityBreakdown:
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
