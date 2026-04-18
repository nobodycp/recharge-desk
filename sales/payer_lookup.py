"""Helpers for payer name lookup from historical sales (employee entry UX)."""

from __future__ import annotations

from typing import List, Optional, TypedDict

from django.db.models import Count, Max

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


def payer_name_suggestions(query: str, *, limit: int = 10) -> List[PayerSuggestion]:
    """
    Distinct payer names matching query (case-insensitive), ordered by
    frequency then most recently seen.
    """
    q = (query or "").strip()
    if len(q) < 2:
        return []
    lim = max(1, min(limit, 25))
    rows = (
        Sale.objects.exclude(status=Sale.Status.CANCELLED)
        .exclude(payer_name="")
        .filter(payer_name__icontains=q)
        .values("payer_name")
        .annotate(count=Count("id"), last_seen=Max("created_at"))
        .order_by("-count", "-last_seen")[:lim]
    )
    out: List[PayerSuggestion] = []
    for r in rows:
        name = (r["payer_name"] or "").strip()
        if not name:
            continue
        out.append(PayerSuggestion(name=name, count=int(r["count"])))
    return out
