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


MONTHLY_BASE_DAYS = 30


def line_allows_partial_days(product: Product) -> bool:
    line = getattr(product, "line", None)
    return bool(line and getattr(line, "allow_partial_days", False))


def package_allows_day_edit(product: Product) -> bool:
    """Partial days are only editable for monthly packages on opted-in lines."""
    return line_allows_partial_days(product) and package_base_days(product) == MONTHLY_BASE_DAYS


def resolve_duration_days(product: Product, requested_days: int | None) -> int:
    """
    Return the duration that will be charged.

    - Default / disabled / non-monthly packages → full ``base_days``.
    - Monthly package on a line with ``allow_partial_days`` → requested days.
    """
    base = package_base_days(product)
    if not package_allows_day_edit(product):
        return base
    if requested_days is None:
        return base
    days = int(requested_days)
    return days if days >= 1 else base


def prorate_amount(amount: Decimal, *, base_days: int, duration_days: int) -> Decimal:
    """Scale a package amount from ``base_days`` to ``duration_days``."""
    base = base_days if base_days > 0 else 30
    duration = duration_days if duration_days > 0 else base
    if duration == base:
        return quantize_money(amount)
    return quantize_money(Decimal(amount) * Decimal(duration) / Decimal(base))


def provider_comparable_cost(sale) -> Decimal:
    """The part of a sale's cost that is actually owed to / charged by the supplier.

    The new-SIM chip is our own stock, not a supplier charge, so it is excluded
    only when ``cost_price_snapshot`` actually contains it (package + chip).
    Older new-SIM sales stored the package price alone; subtracting the chip
    from those snapshots removes a real supplier charge. eSIM extra stays,
    because that is a supplier cost.

    This is both the amount deducted from / restored to the company's
    provider balance (see ``sales.services.create_sale`` / ``cancel_sale``)
    and the figure compared against the supplier's own balance report during
    reconciliation — the two must agree, or every new-SIM sale would look
    like an unexplained mismatch.
    """
    cost = quantize_money(Decimal(sale.cost_price_snapshot or 0))
    if not getattr(sale, "is_new_sim", False):
        return cost
    product = getattr(sale, "product", None)
    chip = _new_sim_unit_cost(product) if product is not None else Decimal("0")
    if chip <= 0 or not _snapshot_includes_new_sim_chip(sale, chip):
        return cost
    if cost <= chip:
        return Decimal("0.00")
    return quantize_money(cost - chip)


def _snapshot_includes_new_sim_chip(sale, chip: Decimal) -> bool:
    """True when the recorded cost is the supplier package plus the chip."""
    product = getattr(sale, "product", None)
    if product is None:
        return False
    package = _supplier_package_cost(sale)
    recorded = quantize_money(Decimal(sale.cost_price_snapshot or 0))
    return quantize_money(package + chip) == recorded


def _supplier_package_cost(sale) -> Decimal:
    """Package price the supplier charges, before any new-SIM chip."""
    product = sale.product
    base = package_base_days(product)
    raw_days = getattr(sale, "duration_days", None)
    days = int(raw_days) if raw_days else base
    cost = prorate_amount(product.cost_price, base_days=base, duration_days=days)
    if getattr(sale, "is_esim", False):
        cost += ESIM_EXTRA_COST
    return cost


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
    Total cost of the sale, stored on ``Sale.cost_price_snapshot`` and used for
    profit/loss. Only part of this — see ``provider_comparable_cost`` — is
    actually deducted from the company's provider balance: the new-SIM chip
    cost is real (it lowers profit) but it is paid for out of our own SIM
    stock, not out of the supplier's balance.

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
