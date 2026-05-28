"""Resolve SIM stock to a shared product line (by line name, not supplier company)."""

from __future__ import annotations

from companies.models import ProductLine


def canonical_product_line(line: ProductLine) -> ProductLine:
    """One stock pool per product-line name (e.g. Sleekom under Sky and Areen)."""
    name = (line.name or "").strip()
    if not name:
        return line
    canonical = (
        ProductLine.objects.filter(is_active=True, name__iexact=name)
        .order_by("pk")
        .first()
    )
    return canonical or line


def distinct_sim_product_lines():
    """Active lines deduplicated by name — used in inventory dropdowns and overview."""
    seen: dict[str, ProductLine] = {}
    for line in ProductLine.objects.filter(is_active=True).select_related("company").order_by(
        "name", "pk"
    ):
        key = (line.name or "").strip().casefold()
        if key and key not in seen:
            seen[key] = line
    return list(seen.values())
