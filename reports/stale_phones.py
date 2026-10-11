"""Helpers for the “lines for disconnect” management report."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.db.models import Max
from django.db.models.functions import Lower, Trim
from django.utils import timezone

from sales.models import Sale

from reports.models import PhoneLineTracking

MONTHLY_FALLBACK_DAYS = 30


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


def sale_duration_days(sale: Sale) -> int:
    """Days the latest sale covers (charged duration, else package base, else 30)."""
    days = getattr(sale, "duration_days", None)
    if days is not None and int(days) >= 1:
        return int(days)
    base = getattr(getattr(sale, "product", None), "base_days", None)
    if base is not None and int(base) >= 1:
        return int(base)
    return MONTHLY_FALLBACK_DAYS


def stale_reference_aggregate(*, threshold_days: int) -> list[dict[str, Any]]:
    """
    One row per phone/reference that is due for disconnect review.

    A number appears when::

        last_sale_date + duration_days + threshold_days  <=  now

    i.e. the subscription period has ended and the activity threshold
    (idle days after end) has also elapsed.

    Rules:
    - Uses the **latest** non-cancelled sale only (a new charge resets the clock).
    - Latest sale marked **eSIM** is excluded (physical-SIM disconnect list).
    """
    # Prefilter: anything newer than ``threshold_days`` cannot be due yet
    # (even with a 1-day duration). Longer durations are refined below.
    cutoff = timezone.now() - timedelta(days=max(1, int(threshold_days)))
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

    latest_by_key = _latest_sale_per_ref_key(keys)
    now = timezone.now()
    out: list[dict[str, Any]] = []
    for row in raw_rows:
        key = row["ref_key"] or ""
        if not key or key in dismissed:
            continue
        sale = latest_by_key.get(key)
        if not sale:
            continue
        # eSIM lines are not managed on the physical disconnect list.
        if sale.is_esim:
            continue
        last = row["last_activity"] or sale.created_at
        duration = sale_duration_days(sale)
        subscription_end = last + timedelta(days=duration)
        disconnect_at = subscription_end + timedelta(days=int(threshold_days))
        if now < disconnect_at:
            continue
        days_past_end = max(0, (now - subscription_end).days)
        meta = metas.get(key)
        line = sale.product.line
        line_name = line.name if line else ""
        out.append(
            {
                "ref_key": key,
                "last_activity": last,
                "display_reference": display_map.get(key, key),
                "sim_identifier": (meta.sim_identifier if meta else "") or "",
                "tracking_id": meta.pk if meta else None,
                "duration_days": duration,
                "subscription_end": subscription_end,
                "disconnect_at": disconnect_at,
                "days_past_end": days_past_end,
                # Alias used by existing sort keys / templates.
                "idle_days": days_past_end,
                "customer_or_payer": (
                    sale.customer.name if sale.customer_id else sale.payer_name
                ),
                "company_name": sale.company.name,
                "company_id": sale.company_id,
                "line_name": line_name,
                "line_name_key": normalize_reference_key(line_name),
                "product_display": line_name,
                "product_id": sale.product_id,
            }
        )
    return out


def filter_stale_rows(
    rows: list[dict[str, Any]],
    *,
    cleaned: dict[str, Any] | None,
    q: str,
) -> list[dict[str, Any]]:
    """Apply company / unified line-name filters and free-text ``q``."""
    cleaned = cleaned or {}
    out = list(rows)
    company = cleaned.get("company")
    if company:
        cid = company.pk
        out = [r for r in out if r.get("company_id") == cid]
    line_name = (cleaned.get("line_name") or "").strip().lower()
    if line_name:
        out = [r for r in out if (r.get("line_name_key") or "") == line_name]
    needle = (q or "").strip().lower()
    if needle:

        def matches(r: dict[str, Any]) -> bool:
            parts = (
                str(r.get("display_reference") or ""),
                str(r.get("ref_key") or ""),
                str(r.get("customer_or_payer") or ""),
                str(r.get("company_name") or ""),
                str(r.get("line_name") or ""),
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
        "idle_days": lambda r: r.get("days_past_end", r.get("idle_days", 0)),
        "days_past_end": lambda r: r.get("days_past_end", 0),
        "last_activity": lambda r: r.get("last_activity"),
        "subscription_end": lambda r: r.get("subscription_end"),
        "customer": lambda r: (r.get("customer_or_payer") or "").lower(),
        "company": lambda r: (r.get("company_name") or "").lower(),
        "product": lambda r: (r.get("line_name") or r.get("product_display") or "").lower(),
        "line": lambda r: (r.get("line_name") or "").lower(),
        "sim": lambda r: (r.get("sim_identifier") or "").lower(),
        "duration": lambda r: r.get("duration_days", 0),
    }
    key_fn = sort_keys.get(sort) or sort_keys["days_past_end"]
    return sorted(rows, key=key_fn, reverse=reverse)
