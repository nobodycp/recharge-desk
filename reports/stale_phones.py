"""Helpers for the phone line statement (بيان الأرقام) and dashboard KPIs."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from django.db.models import Max
from django.db.models.functions import Lower, Trim
from django.utils import timezone

from sales.models import Sale

from reports.models import PhoneLineTracking

MONTHLY_FALLBACK_DAYS = 30
MANUAL_RENEW_DAYS = 1

# Lifecycle status keys (shared by dashboard counts + phone lines statement).
STATUS_ACTIVE = "active"
STATUS_DUE_SOON = "due_soon"
STATUS_ENDED_UNDER = "ended_under_threshold"
STATUS_OVERDUE = "overdue"


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


def effective_activity_clock(
    *,
    sale: Sale,
    sale_last_activity: datetime,
    meta: PhoneLineTracking | None,
) -> tuple[datetime, int, bool]:
    """
    Return ``(activity_start, duration_days, used_manual_renew)``.

    A manual renew (virtual 1-day charge) wins when it is at least as new as
    the latest real sale activity.
    """
    last = sale_last_activity or sale.created_at
    duration = sale_duration_days(sale)
    reset_at = getattr(meta, "manual_reset_at", None) if meta else None
    if reset_at is not None and reset_at >= last:
        return reset_at, MANUAL_RENEW_DAYS, True
    return last, duration, False


def _lifecycle_status(
    *,
    now,
    subscription_end,
    disconnect_at,
    soon_end,
) -> str:
    """
    Mutually exclusive stages:

    - overdue: past inactivity threshold
    - ended_under: subscription already ended, still inside grace
    - due_soon: charge still running but ends within ``soon_end``
      (typically 24h before subscription end)
    - active: charge running with more than that window left
    """
    if disconnect_at <= now:
        return STATUS_OVERDUE
    if subscription_end <= now:
        return STATUS_ENDED_UNDER
    if subscription_end <= soon_end:
        return STATUS_DUE_SOON
    return STATUS_ACTIVE


def phone_lines_aggregate(
    *, threshold_days: int, due_soon_hours: int = 24
) -> list[dict[str, Any]]:
    """
    One row per phone/reference that has any non-cancelled sale.

    Includes active / grace / due-soon / overdue and eSIM lines. Each row
    carries a ``status`` key matching the dashboard lifecycle buckets.
    """
    threshold_days = max(1, int(threshold_days))
    due_soon_hours = max(0, int(due_soon_hours))
    base = Sale.objects.exclude(status=Sale.Status.CANCELLED).annotate(
        ref_key=Lower(Trim("reference_number"))
    )
    grouped = list(
        base.values("ref_key")
        .annotate(last_activity=Max("created_at"))
        .exclude(ref_key="")
    )
    keys = [r["ref_key"] for r in grouped if r["ref_key"]]
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
        for m in PhoneLineTracking.objects.filter(reference_key__in=keys)
    }
    latest_by_key = _latest_sale_per_ref_key(keys)
    now = timezone.now()
    soon_end = now + timedelta(hours=due_soon_hours)
    out: list[dict[str, Any]] = []
    for row in grouped:
        key = row["ref_key"] or ""
        if not key:
            continue
        sale = latest_by_key.get(key)
        if not sale:
            continue
        meta = metas.get(key)
        last, duration, used_manual = effective_activity_clock(
            sale=sale,
            sale_last_activity=row["last_activity"] or sale.created_at,
            meta=meta,
        )
        subscription_end = last + timedelta(days=duration)
        disconnect_at = subscription_end + timedelta(days=threshold_days)
        status = _lifecycle_status(
            now=now,
            subscription_end=subscription_end,
            disconnect_at=disconnect_at,
            soon_end=soon_end,
        )
        days_past_end = max(0, (now - subscription_end).days)
        line = sale.product.line
        line_name = line.name if line else ""
        out.append(
            {
                "ref_key": key,
                "last_activity": last,
                "display_reference": display_map.get(key, key),
                "sim_identifier": (meta.sim_identifier if meta else "") or "",
                "tracking_id": meta.pk if meta else None,
                "is_esim": bool(sale.is_esim),
                "used_manual_renew": used_manual,
                "status": status,
                "duration_days": duration,
                "subscription_end": subscription_end,
                "disconnect_at": disconnect_at,
                "days_past_end": days_past_end,
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


def reference_disconnect_counts(
    *, threshold_days: int, due_soon_hours: int = 24
) -> dict[str, int]:
    """
    Lifecycle counts for the operational dashboard (physical SIMs only).
    Buckets are mutually exclusive:

    - ``active``: charge still running with more than ``due_soon_hours`` left
    - ``due_soon``: last charge ends within ``due_soon_hours``
      (the day before subscription end)
    - ``ended_under_threshold``: subscription ended, still in grace
    - ``overdue``: past activity threshold

    ``esim_active`` is the eSIM share of ``active`` (Active total =
    physical active + eSIM active). Non-active eSIMs stay excluded.
    """
    threshold_days = max(1, int(threshold_days))
    due_soon_hours = max(0, int(due_soon_hours))
    base = Sale.objects.exclude(status=Sale.Status.CANCELLED).annotate(
        ref_key=Lower(Trim("reference_number"))
    )
    grouped = list(
        base.values("ref_key")
        .annotate(last_activity=Max("created_at"))
        .exclude(ref_key="")
    )
    keys = [r["ref_key"] for r in grouped if r["ref_key"]]
    latest_by_key = _latest_sale_per_ref_key(keys)
    metas = {
        m.reference_key: m
        for m in PhoneLineTracking.objects.filter(reference_key__in=keys)
    }
    now = timezone.now()
    soon_end = now + timedelta(hours=due_soon_hours)
    counts = {
        STATUS_ACTIVE: 0,
        STATUS_DUE_SOON: 0,
        STATUS_ENDED_UNDER: 0,
        STATUS_OVERDUE: 0,
    }
    esim_active = 0
    for row in grouped:
        key = row["ref_key"] or ""
        if not key:
            continue
        sale = latest_by_key.get(key)
        if not sale:
            continue
        last, duration, _used = effective_activity_clock(
            sale=sale,
            sale_last_activity=row["last_activity"] or sale.created_at,
            meta=metas.get(key),
        )
        subscription_end = last + timedelta(days=duration)
        disconnect_at = subscription_end + timedelta(days=threshold_days)
        status = _lifecycle_status(
            now=now,
            subscription_end=subscription_end,
            disconnect_at=disconnect_at,
            soon_end=soon_end,
        )
        if sale.is_esim:
            # Only Active eSIMs join the Active bucket (and total / donut).
            # Other eSIM lifecycle stages stay out of the physical counts.
            if status == STATUS_ACTIVE:
                esim_active += 1
                counts[STATUS_ACTIVE] += 1
            continue
        counts[status] += 1
    total = sum(counts.values())
    return {
        "active": counts[STATUS_ACTIVE],
        "due_soon": counts[STATUS_DUE_SOON],
        "ended_under_threshold": counts[STATUS_ENDED_UNDER],
        "overdue": counts[STATUS_OVERDUE],
        "esim_active": esim_active,
        "total": total,
    }


def filter_stale_rows(
    rows: list[dict[str, Any]],
    *,
    cleaned: dict[str, Any] | None,
    q: str,
) -> list[dict[str, Any]]:
    """Apply company / unified line-name / status filters and free-text ``q``."""
    cleaned = cleaned or {}
    out = list(rows)
    company = cleaned.get("company")
    if company:
        cid = company.pk
        out = [r for r in out if r.get("company_id") == cid]
    line_name = (cleaned.get("line_name") or "").strip().lower()
    if line_name:
        out = [r for r in out if (r.get("line_name_key") or "") == line_name]
    status = (cleaned.get("status") or "").strip().lower()
    if status:
        out = [r for r in out if (r.get("status") or "") == status]
    sim_type = (cleaned.get("sim_type") or "").strip().lower()
    if sim_type == "esim":
        out = [r for r in out if r.get("is_esim")]
    elif sim_type == "physical":
        out = [r for r in out if not r.get("is_esim")]
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


_STATUS_SORT_ORDER = {
    STATUS_OVERDUE: 0,
    STATUS_DUE_SOON: 1,
    STATUS_ENDED_UNDER: 2,
    STATUS_ACTIVE: 3,
}


def sort_stale_rows(
    rows: list[dict[str, Any]], *, sort: str, order: str
) -> list[dict[str, Any]]:
    """Sort in-memory phone-line rows (``sort`` / ``order`` match other grids)."""
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
        "status": lambda r: _STATUS_SORT_ORDER.get(r.get("status"), 99),
    }
    key_fn = sort_keys.get(sort) or sort_keys["days_past_end"]
    return sorted(rows, key=key_fn, reverse=reverse)
