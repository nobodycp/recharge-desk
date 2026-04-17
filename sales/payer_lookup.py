"""Helpers for payer name lookup from historical sales (employee entry UX)."""

from __future__ import annotations

from typing import List, TypedDict

from django.db.models import Count, Max

from sales.models import Sale


def latest_payer_for_reference(reference: str) -> str | None:
    """
    Latest non-empty payer_name for an exact reference_number match.
    Excludes cancelled sales.
    """
    ref = (reference or "").strip()
    if not ref:
        return None
    row = (
        Sale.objects.exclude(status=Sale.Status.CANCELLED)
        .exclude(payer_name="")
        .filter(reference_number=ref)
        .order_by("-created_at", "-id")
        .values_list("payer_name", flat=True)
        .first()
    )
    if not row:
        return None
    cleaned = row.strip()
    return cleaned or None


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
