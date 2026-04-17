"""Centralized sale cost rules (eSIM surcharge, etc.)."""

from __future__ import annotations

from decimal import Decimal

from companies.models import Product

# Extra supplier cost applied when a sale is marked as eSIM.
ESIM_EXTRA_COST = Decimal("5")


def effective_cost_for_product(product: Product, *, is_esim: bool) -> Decimal:
    """
    Cost deducted from company balance and stored on ``Sale.cost_price_snapshot``.

    When ``is_esim`` is True, ``ESIM_EXTRA_COST`` is added to the product's catalog cost.
    """
    base = product.cost_price
    return base + ESIM_EXTRA_COST if is_esim else base


def loss_snapshot_for_sale(*, sell_price_actual: Decimal, cost_price_snapshot: Decimal) -> Decimal:
    """
    Explicit loss magnitude when the sale is recorded at **zero** revenue.

    The supplier cost is still deducted from balance; this field stores that cost as
    a positive "loss" amount for reporting (distinct from negative ``profit_snapshot``).
    """
    if sell_price_actual == 0:
        return cost_price_snapshot
    return Decimal("0")
