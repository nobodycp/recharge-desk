"""Helpers for the phone line statement (بيان الأرقام) and dashboard KPIs."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from django.db import transaction
from django.db.models import F, Subquery, Window
from django.db.models.functions import Lower, RowNumber, Trim
from django.utils import timezone
from django.utils.translation import gettext as _

from sales.models import Sale

from reports.models import PhoneLineTracking

MONTHLY_FALLBACK_DAYS = 30
MANUAL_RENEW_DAYS = 1

# Lifecycle status keys (shared by dashboard counts + phone lines statement).
STATUS_ACTIVE = "active"
STATUS_DUE_SOON = "due_soon"
STATUS_ENDED_UNDER = "ended_under_threshold"
STATUS_OVERDUE = "overdue"


def _latest_line_sales() -> list[Sale]:
    """One non-cancelled sale per reference: the newest by created_at, then pk.

    A window keeps this to a single row per line. Loading every historical
    sale (the old path) made delete and the statement wait on the whole ledger.
    """
    base = (
        Sale.objects.exclude(status=Sale.Status.CANCELLED)
        .annotate(ref_key=Lower(Trim("reference_number")))
        .exclude(ref_key="")
    )
    ranked = base.annotate(
        rn=Window(
            expression=RowNumber(),
            partition_by=[F("ref_key")],
            order_by=[F("created_at").desc(), F("pk").desc()],
        )
    )
    return list(
        Sale.objects.filter(pk__in=Subquery(ranked.filter(rn=1).values("pk")))
        .annotate(ref_key=Lower(Trim("reference_number")))
        .select_related("company", "customer", "product", "product__line")
    )


def _latest_sale_for_key(key: str) -> Sale | None:
    """Newest non-cancelled sale for one normalized reference. None if absent."""
    if not key:
        return None
    return (
        Sale.objects.exclude(status=Sale.Status.CANCELLED)
        .annotate(ref_key=Lower(Trim("reference_number")))
        .filter(ref_key=key)
        .select_related("company", "customer", "product", "product__line")
        .order_by("-created_at", "-pk")
        .first()
    )


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


def _statement_row(
    *,
    sale: Sale,
    last_activity,
    meta: PhoneLineTracking | None,
    threshold_days: int,
    now,
    soon_end,
) -> dict[str, Any]:
    """One statement row from the line's latest sale."""
    key = sale.ref_key
    last, duration, used_manual = effective_activity_clock(
        sale=sale,
        sale_last_activity=last_activity or sale.created_at,
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
    return {
        "ref_key": key,
        "last_activity": last,
        "display_reference": sale.reference_number or key,
        "sim_identifier": (meta.sim_identifier if meta else "") or "",
        "notes": (meta.notes if meta else "") or "",
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


def phone_lines_aggregate(
    *, threshold_days: int, due_soon_hours: int = 24
) -> list[dict[str, Any]]:
    """
    One row per phone/reference that has any non-cancelled sale.

    Includes active / grace / due-soon / overdue and eSIM lines. Rows with
    ``PhoneLineTracking.is_dismissed`` (Delete on the statement) are omitted;
    a new sale clears that flag so the number can return. Each row carries a
    ``status`` key matching the dashboard lifecycle buckets.
    """
    threshold_days = max(1, int(threshold_days))
    due_soon_hours = max(0, int(due_soon_hours))
    sales = _latest_line_sales()
    if not sales:
        return []
    keys = [s.ref_key for s in sales if s.ref_key]
    metas = {
        m.reference_key: m
        for m in PhoneLineTracking.objects.filter(reference_key__in=keys)
    }
    dismissed_keys = {
        k for k, m in metas.items() if getattr(m, "is_dismissed", False)
    }
    now = timezone.now()
    soon_end = now + timedelta(hours=due_soon_hours)
    out: list[dict[str, Any]] = []
    for sale in sales:
        key = sale.ref_key or ""
        if not key or key in dismissed_keys:
            continue
        out.append(
            _statement_row(
                sale=sale,
                last_activity=sale.created_at,
                meta=metas.get(key),
                threshold_days=threshold_days,
                now=now,
                soon_end=soon_end,
            )
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
    sales = _latest_line_sales()
    keys = [s.ref_key for s in sales if s.ref_key]
    metas = {
        m.reference_key: m
        for m in PhoneLineTracking.objects.filter(reference_key__in=keys)
    }
    dismissed_keys = {
        k for k, m in metas.items() if getattr(m, "is_dismissed", False)
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
    for sale in sales:
        key = sale.ref_key or ""
        if not key or key in dismissed_keys:
            continue
        last, duration, _used = effective_activity_clock(
            sale=sale,
            sale_last_activity=sale.created_at,
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
                str(r.get("notes") or ""),
            )
            return any(needle in p.lower() for p in parts)

        out = [r for r in out if matches(r)]
    return out


@transaction.atomic
def dismiss_phone_line(*, ref_key: str, display_reference: str = "") -> PhoneLineTracking:
    """Remove a number from بيان الأرقام / dashboard counts (keeps sale history)."""
    key = normalize_reference_key(ref_key)
    if not key:
        raise ValueError(_("Invalid phone number."))
    meta, _created = PhoneLineTracking.objects.update_or_create(
        reference_key=key,
        defaults={
            "is_dismissed": True,
            "last_display_reference": (display_reference or key)[:64],
        },
    )
    if not meta.is_dismissed:
        meta.is_dismissed = True
        meta.save(update_fields=["is_dismissed", "updated_at"])
    return meta


def normalize_ref_keys(raw_keys) -> list[str]:
    """Unique normalized keys, first-seen order."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in raw_keys:
        key = normalize_reference_key(raw)
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


@transaction.atomic
def dismiss_phone_lines(keys: list[str]) -> list[str]:
    """Dismiss every key that still has a non-cancelled sale. Returns those keys."""
    keys = normalize_ref_keys(keys)
    if not keys:
        return []
    found = list(
        Sale.objects.exclude(status=Sale.Status.CANCELLED)
        .annotate(ref_key=Lower(Trim("reference_number")))
        .filter(ref_key__in=keys)
        .values_list("ref_key", flat=True)
        .distinct()
    )
    if not found:
        return []
    existing = {
        m.reference_key: m
        for m in PhoneLineTracking.objects.filter(reference_key__in=found)
    }
    to_create = []
    to_update = []
    dismissed: list[str] = []
    for key in found:
        meta = existing.get(key)
        if meta is None:
            to_create.append(
                PhoneLineTracking(
                    reference_key=key,
                    is_dismissed=True,
                    last_display_reference=key[:64],
                )
            )
            dismissed.append(key)
        elif not meta.is_dismissed:
            meta.is_dismissed = True
            to_update.append(meta)
            dismissed.append(key)
        else:
            dismissed.append(key)
    if to_create:
        PhoneLineTracking.objects.bulk_create(to_create)
    if to_update:
        PhoneLineTracking.objects.bulk_update(to_update, ["is_dismissed"])
    return dismissed


def phone_line_row(*, ref_key: str, threshold_days: int, due_soon_hours: int = 24) -> dict | None:
    """One statement row, without building the full inventory."""
    key = normalize_reference_key(ref_key)
    sale = _latest_sale_for_key(key)
    if not sale:
        return None
    meta = PhoneLineTracking.objects.filter(reference_key=key).first()
    if meta is not None and meta.is_dismissed:
        return None
    now = timezone.now()
    return _statement_row(
        sale=sale,
        last_activity=sale.created_at,
        meta=meta,
        threshold_days=max(1, int(threshold_days)),
        now=now,
        soon_end=now + timedelta(hours=max(0, int(due_soon_hours))),
    )


@transaction.atomic
def rename_phone_line(
    *,
    old_key: str,
    new_reference: str,
    sim_identifier: str = "",
    notes: str = "",
    reset_countdown: bool = True,
) -> str:
    """Rename a tracked phone across sales + tracking; optionally reset disconnect clock.

    Refused (``ValueError``) while any of the number's sales has an open
    provider charge, since the charge was sent for the current number.
    Returns the new normalized ``ref_key``.
    """
    old = normalize_reference_key(old_key)
    new_display = (new_reference or "").strip()
    new = normalize_reference_key(new_display)
    if not old or not new:
        raise ValueError(_("Enter a phone number or reference."))

    sale_ids = list(
        Sale.objects.exclude(status=Sale.Status.CANCELLED)
        .annotate(rk=Lower(Trim("reference_number")))
        .filter(rk=old)
        .values_list("pk", flat=True)
    )
    if not sale_ids:
        raise ValueError(_("No sales found for this number."))

    if new != old:
        conflict = (
            Sale.objects.exclude(status=Sale.Status.CANCELLED)
            .annotate(rk=Lower(Trim("reference_number")))
            .filter(rk=new)
            .exclude(pk__in=sale_ids)
            .exists()
        )
        if conflict:
            raise ValueError(_("Another line already uses this number."))

    open_charge = (
        Sale.objects.filter(
            pk__in=sale_ids,
            entry_mode=Sale.EntryMode.AUTO_CHARGE,
            charge_status__in=(Sale.ChargeStatus.PROCESSING, Sale.ChargeStatus.UNVERIFIED),
        )
        .exclude(reference_number=new_display)
        .exists()
    )
    if open_charge:
        raise ValueError(
            _("This number has a charge still in progress; try again after it settles.")
        )

    Sale.objects.filter(pk__in=sale_ids).update(reference_number=new_display)

    now = timezone.now() if reset_countdown else None
    old_meta = PhoneLineTracking.objects.filter(reference_key=old).first()
    defaults = {
        "sim_identifier": (sim_identifier or "").strip(),
        "notes": (notes or "").strip(),
        "last_display_reference": new_display[:64],
        "is_dismissed": False,
    }
    if reset_countdown:
        defaults["manual_reset_at"] = now

    if new == old:
        if not reset_countdown and old_meta is not None:
            defaults["manual_reset_at"] = old_meta.manual_reset_at
        PhoneLineTracking.objects.update_or_create(
            reference_key=old, defaults=defaults
        )
    else:
        if old_meta is not None:
            if not reset_countdown:
                defaults["manual_reset_at"] = old_meta.manual_reset_at
            old_meta.delete()
        PhoneLineTracking.objects.update_or_create(
            reference_key=new, defaults=defaults
        )
    return new


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
