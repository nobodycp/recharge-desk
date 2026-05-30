"""Helpers for payer name lookup from historical sales (employee entry UX)."""

from __future__ import annotations

from typing import List, Optional, TypedDict

from django.db.models import Count, Max

from customers.models import Customer
from sales.models import Sale


class LatestSaleSnapshot(TypedDict):
    """Compact view of the most recent (non-cancelled) sale for a reference."""

    payer_name: Optional[str]
    company_id: Optional[int]
    product_id: Optional[int]


def latest_sale_for_reference(reference: str) -> Optional[LatestSaleSnapshot]:
    """
    Latest non-cancelled sale matching an exact reference_number.

    Returns the payer name plus the company/product IDs so the employee
    form can pre-fill the most likely choices on a known number, while
    still allowing manual override.
    """
    ref = (reference or "").strip()
    if not ref:
        return None
    row = (
        Sale.objects.exclude(status=Sale.Status.CANCELLED)
        .filter(reference_number=ref)
        .order_by("-created_at", "-id")
        .values("payer_name", "company_id", "product_id")
        .first()
    )
    if not row:
        return None
    payer = (row.get("payer_name") or "").strip()
    return LatestSaleSnapshot(
        payer_name=payer or None,
        company_id=row.get("company_id"),
        product_id=row.get("product_id"),
    )


def latest_payer_for_reference(reference: str) -> str | None:
    """
    Latest non-empty payer_name for an exact reference_number match.
    Excludes cancelled sales.

    Kept for callers that only need the name; new code should prefer
    :func:`latest_sale_for_reference` to also retrieve company/product hints.
    """
    snap = latest_sale_for_reference(reference)
    if not snap:
        return None
    return snap.get("payer_name") or None


class PayerSuggestion(TypedDict):
    name: str
    count: int
    is_customer_account: bool


def payer_name_suggestions(query: str, *, limit: int = 10) -> List[PayerSuggestion]:
    """
    Distinct payer names matching query (case-insensitive), ordered by
    sale frequency then name. Active customer account names are merged in
    when they are not already present from sales history.

    ``is_customer_account`` marks registered credit customers so the sales
    entry autocomplete can distinguish them from cash-only payer names.
    """
    q = (query or "").strip()
    if len(q) < 2:
        return []
    lim = max(1, min(limit, 25))
    merged: dict[str, PayerSuggestion] = {}

    customer_account_keys: set[str] = set()
    for name in (
        Customer.objects.filter(is_active=True, name__icontains=q)
        .order_by("name")
        .values_list("name", flat=True)[: lim * 3]
    ):
        clean = (name or "").strip()
        if clean:
            customer_account_keys.add(clean.casefold())

    rows = (
        Sale.objects.exclude(status=Sale.Status.CANCELLED)
        .exclude(payer_name="")
        .filter(payer_name__icontains=q)
        .values("payer_name")
        .annotate(count=Count("id"), last_seen=Max("created_at"))
        .order_by("-count", "-last_seen")
    )
    for r in rows:
        name = (r["payer_name"] or "").strip()
        if not name:
            continue
        key = name.casefold()
        if key in merged:
            continue
        merged[key] = PayerSuggestion(
            name=name,
            count=int(r["count"]),
            is_customer_account=key in customer_account_keys,
        )
        if len(merged) >= lim:
            break

    if len(merged) < lim:
        for name in (
            Customer.objects.filter(is_active=True, name__icontains=q)
            .order_by("name")
            .values_list("name", flat=True)[: lim * 2]
        ):
            clean = (name or "").strip()
            if not clean:
                continue
            key = clean.casefold()
            if key in merged:
                merged[key]["is_customer_account"] = True
                continue
            merged[key] = PayerSuggestion(
                name=clean,
                count=0,
                is_customer_account=True,
            )
            if len(merged) >= lim:
                break

    return sorted(
        merged.values(),
        key=lambda item: (-item["count"], item["name"].casefold()),
    )[:lim]
