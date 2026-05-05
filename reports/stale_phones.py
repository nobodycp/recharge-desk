"""Helpers for the “lines idle for N days” management report."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.db.models import Max
from django.db.models.functions import Lower, Trim
from django.utils import timezone

from sales.models import Sale

from reports.models import PhoneLineTracking


def _latest_sale_per_ref_key(keys: list[str]) -> dict[str, Sale]:
    """Most recent non-cancelled sale per normalized reference key."""
    if not keys:
        return {}
    qs = (
        Sale.objects.exclude(status=Sale.Status.CANCELLED)
        .annotate(ref_key=Lower(Trim("reference_number")))
        .filter(ref_key__in=keys)
        .select_related("company", "customer", "product", "product__line")
        .order_by("ref_key", "-created_at", "-pk")
    )
    out: dict[str, Sale] = {}
    for sale in qs:
        rk = sale.ref_key
        if rk and rk not in out:
            out[rk] = sale
    return out


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

    latest_by_key = _latest_sale_per_ref_key([r["ref_key"] for r in out])
    now = timezone.now()
    for item in out:
        last = item["last_activity"]
        item["idle_days"] = max(0, (now - last).days)
        sale = latest_by_key.get(item["ref_key"])
        if sale:
            item["customer_or_payer"] = (
                sale.customer.name if sale.customer_id else sale.payer_name
            )
            item["company_name"] = sale.company.name
            item["product_display"] = sale.product.display_name
            item["company_id"] = sale.company_id
            item["product_id"] = sale.product_id
        else:
            item["customer_or_payer"] = ""
            item["company_name"] = ""
            item["product_display"] = ""
            item["company_id"] = None
            item["product_id"] = None
    return out


def filter_stale_rows(
    rows: list[dict[str, Any]],
    *,
    cleaned: dict[str, Any] | None,
    q: str,
) -> list[dict[str, Any]]:
    """Apply optional company/product dropdowns and free-text ``q`` (like sales list)."""
    cleaned = cleaned or {}
    out = list(rows)
    company = cleaned.get("company")
    if company:
        cid = company.pk
        out = [r for r in out if r.get("company_id") == cid]
    product = cleaned.get("product")
    if product:
        pid = product.pk
        out = [r for r in out if r.get("product_id") == pid]
    needle = (q or "").strip().lower()
    if needle:

        def matches(r: dict[str, Any]) -> bool:
            parts = (
                str(r.get("display_reference") or ""),
                str(r.get("ref_key") or ""),
                str(r.get("customer_or_payer") or ""),
                str(r.get("company_name") or ""),
                str(r.get("product_display") or ""),
                str(r.get("sim_identifier") or ""),
            )
            return any(needle in p.lower() for p in parts)

        out = [r for r in out if matches(r)]
    return out


def sort_stale_rows(
    rows: list[dict[str, Any]], *, sort: str, order: str
) -> list[dict[str, Any]]:
    """Sort in-memory stale rows (``sort`` / ``order`` match other management grids)."""
    reverse = (order or "").lower() != "asc"
    sort_keys: dict[str, Any] = {
        "ref": lambda r: (r.get("display_reference") or "").lower(),
        "idle_days": lambda r: r.get("idle_days", 0),
        "last_activity": lambda r: r.get("last_activity"),
        "customer": lambda r: (r.get("customer_or_payer") or "").lower(),
        "company": lambda r: (r.get("company_name") or "").lower(),
        "product": lambda r: (r.get("product_display") or "").lower(),
        "sim": lambda r: (r.get("sim_identifier") or "").lower(),
    }
    key_fn = sort_keys.get(sort) or sort_keys["idle_days"]
    return sorted(rows, key=key_fn, reverse=reverse)
