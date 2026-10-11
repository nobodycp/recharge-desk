"""Centralized sale cost rules (eSIM surcharge, new-SIM unit cost, day proration)."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from companies.models import Product

# Extra supplier cost applied when a sale is marked as eSIM.
ESIM_EXTRA_COST = Decimal("5")
_MONEY_QUANT = Decimal("0.01")


def quantize_money(amount: Decimal) -> Decimal:
    return Decimal(amount).quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)


def package_base_days(product: Product) -> int:
    days = int(getattr(product, "base_days", None) or 30)
    return days if days > 0 else 30


def prorate_amount(amount: Decimal, *, base_days: int, duration_days: int) -> Decimal:
    """Scale a package amount from ``base_days`` to ``duration_days``."""
    base = base_days if base_days > 0 else 30
    duration = duration_days if duration_days > 0 else base
    if duration == base:
        return quantize_money(amount)
    return quantize_money(Decimal(amount) * Decimal(duration) / Decimal(base))


def _new_sim_unit_cost(product: Product) -> Decimal:
    """Estimated SIM plastic/chip cost from the product line, if configured."""
    raw = getattr(product.line, "estimated_unit_cost", None)
    if raw is None:
        return Decimal("0")
    cost = Decimal(raw)
    return cost if cost > 0 else Decimal("0")


def effective_cost_for_product(
    product: Product,
    *,
    is_esim: bool,
    is_new_sim: bool = False,
    duration_days: int | None = None,
) -> Decimal:
    """
    Cost deducted from company balance and stored on ``Sale.cost_price_snapshot``.

    Package catalog cost is prorated by ``duration_days / base_days``.
    eSIM and new-SIM extras are fixed add-ons (not prorated).
    """
    base_days = package_base_days(product)
    days = int(duration_days) if duration_days is not None else base_days
    cost = prorate_amount(product.cost_price, base_days=base_days, duration_days=days)
    if is_esim:
        cost += ESIM_EXTRA_COST
    if is_new_sim:
        cost += _new_sim_unit_cost(product)
    return cost


def suggested_sell_for_product(product: Product, *, duration_days: int | None = None) -> Decimal:
    """Default selling price prorated to the requested duration."""
    base_days = package_base_days(product)
    days = int(duration_days) if duration_days is not None else base_days
    return prorate_amount(
        product.default_sell_price,
        base_days=base_days,
        duration_days=days,
    )


def loss_snapshot_for_sale(*, sell_price_actual: Decimal, cost_price_snapshot: Decimal) -> Decimal:
    """
    Explicit loss magnitude when the sale is recorded at **zero** revenue.

    The supplier cost is still deducted from balance; this field stores that cost as
    a positive "loss" amount for reporting (distinct from negative ``profit_snapshot``).
    """
    if sell_price_actual == 0:
        return cost_price_snapshot
    return Decimal("0")
