"""Helpers for the “lines idle for N days” management report."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.db.models import Max
from django.db.models.functions import Lower, Trim
from django.utils import timezone

from sales.models import Sale

from reports.models import PhoneLineTracking


def normalize_reference_key(raw: str) -> str:
    return (raw or "").strip().lower()


def stale_reference_aggregate(*, threshold_days: int) -> list[dict[str, Any]]:
    """Return one row per reference_number key with last non-cancelled activity.

    Only rows whose ``last_activity`` is strictly before ``now - threshold_days``
    are returned, excluding numbers the user dismissed from this report.
    """
    cutoff = timezone.now() - timedelta(days=threshold_days)
    dismissed = set(
        PhoneLineTracking.objects.filter(is_dismissed=True).values_list(
            "reference_key", flat=True
        )
    )
    base = Sale.objects.exclude(status=Sale.Status.CANCELLED).annotate(
        ref_key=Lower(Trim("reference_number"))
    )
    grouped = (
        base.values("ref_key")
        .annotate(last_activity=Max("created_at"))
        .filter(last_activity__lt=cutoff)
        .exclude(ref_key="")
        .order_by("last_activity")
    )
    raw_rows = list(grouped)
    keys = [
        r["ref_key"]
        for r in raw_rows
        if r["ref_key"] and r["ref_key"] not in dismissed
    ]
    if not keys:
        return []

    display_map: dict[str, str] = {}
    for row in (
        base.filter(ref_key__in=keys)
        .order_by("-created_at")
        .values("ref_key", "reference_number")
    ):
        k = row["ref_key"]
        if k and k not in display_map:
            display_map[k] = row["reference_number"] or k

    metas = {
        m.reference_key: m
        for m in PhoneLineTracking.objects.filter(
            reference_key__in=keys, is_dismissed=False
        )
    }

    out: list[dict[str, Any]] = []
    for row in raw_rows:
        key = row["ref_key"] or ""
        if not key or key in dismissed:
            continue
        meta = metas.get(key)
        out.append(
            {
                "ref_key": key,
                "last_activity": row["last_activity"],
                "display_reference": display_map.get(key, key),
                "sim_identifier": (meta.sim_identifier if meta else "") or "",
                "tracking_id": meta.pk if meta else None,
            }
        )
    return out
