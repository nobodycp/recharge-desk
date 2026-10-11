"""Centralized sale cost rules (eSIM surcharge, new-SIM unit cost, etc.)."""

from __future__ import annotations

from decimal import Decimal

from companies.models import Product

# Extra supplier cost applied when a sale is marked as eSIM.
ESIM_EXTRA_COST = Decimal("5")


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
) -> Decimal:
    """
    Cost deducted from company balance and stored on ``Sale.cost_price_snapshot``.

    - When ``is_esim`` is True, ``ESIM_EXTRA_COST`` is added to the product's catalog cost.
    - When ``is_new_sim`` is True, the product line's ``estimated_unit_cost`` (if set)
      is also added so new-SIM sales reduce profit by the chip cost.
    """
    cost = product.cost_price
    if is_esim:
        cost += ESIM_EXTRA_COST
    if is_new_sim:
        cost += _new_sim_unit_cost(product)
    return cost


def loss_snapshot_for_sale(*, sell_price_actual: Decimal, cost_price_snapshot: Decimal) -> Decimal:
    """
    Explicit loss magnitude when the sale is recorded at **zero** revenue.

    The supplier cost is still deducted from balance; this field stores that cost as
    a positive "loss" amount for reporting (distinct from negative ``profit_snapshot``).
    """
    if sell_price_actual == 0:
        return cost_price_snapshot
    return Decimal("0")
