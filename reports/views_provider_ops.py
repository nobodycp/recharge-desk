"""«تقارير المزودين»: report over the provider operations log."""
from __future__ import annotations

import re
from datetime import datetime, time, timedelta

from django.db.models import Count, Q
from django.shortcuts import render
from django.utils import timezone

from accounts.permissions import management_required
from core.pagination import paginate_request
from providers.models import ProviderOperation
from providers.operations import (
    CAUSE_LABELS,
    OUTCOME_FAILED,
    OUTCOME_LABELS,
    OUTCOME_PENDING,
    OUTCOME_SUCCESS,
    SERVICE_LABELS,
    SOURCE_LABELS,
)
from providers.registry import ADAPTERS

PAGE_SIZE = 50
DEFAULT_PERIOD = "7d"
PERIOD_LABELS = {
    "today": "اليوم",
    "7d": "آخر 7 أيام",
    "30d": "آخر 30 يومًا",
    "custom": "فترة مخصصة",
}
TOP_CAUSES = 5
_MESSAGE_PREFIX = re.compile(r"^فشل .+? — ")


def _parse_date(raw):
    try:
        day = datetime.strptime((raw or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None
    return day if 2000 <= day.year <= 2100 else None


def _local_midnight(day):
    return timezone.make_aware(datetime.combine(day, time.min))


def _resolve_period(params):
    """Return ``(period, date_from, date_to)``; bounds are local dates, inclusive."""
    period = params.get("period") or DEFAULT_PERIOD
    today = timezone.localdate()
    if period == "custom":
        start, end = _parse_date(params.get("from")), _parse_date(params.get("to"))
        if start and end and start <= end:
            return "custom", start, end
        period = DEFAULT_PERIOD
    if period == "today":
        return "today", today, today
    if period == "30d":
        return "30d", today - timedelta(days=29), today
    return DEFAULT_PERIOD, today - timedelta(days=6), today


def _cause_text(error_code, message):
    if error_code in CAUSE_LABELS:
        return CAUSE_LABELS[error_code]
    stripped = _MESSAGE_PREFIX.sub("", message or "", count=1).strip()
    return stripped or error_code or "غير محدد"


def _top_causes(qs):
    rows = (
        qs.filter(outcome=OUTCOME_FAILED)
        .values("error_code")
        .annotate(n=Count("id"))
        .order_by("-n", "error_code")[:TOP_CAUSES]
    )
    causes = []
    for row in rows:
        code = row["error_code"]
        message = ""
        if code not in CAUSE_LABELS:
            message = (
                qs.filter(outcome=OUTCOME_FAILED, error_code=code)
                .values_list("message", flat=True)
                .first()
            )
        causes.append((_cause_text(code, message), row["n"]))
    return causes


def _per_service(qs):
    stats = {
        row["service"]: row
        for row in qs.values("service").annotate(
            n=Count("id"), ok=Count("id", filter=Q(outcome=OUTCOME_SUCCESS))
        )
    }
    ordered = [s for s in SERVICE_LABELS if s in stats]
    ordered += sorted(s for s in stats if s not in SERVICE_LABELS)
    return [
        (
            SERVICE_LABELS.get(s, s),
            stats[s]["n"],
            round(stats[s]["ok"] * 100 / stats[s]["n"]) if stats[s]["n"] else 0,
        )
        for s in ordered
    ]


def _summary(qs):
    agg = qs.aggregate(
        total=Count("id"),
        success=Count("id", filter=Q(outcome=OUTCOME_SUCCESS)),
        failed=Count("id", filter=Q(outcome=OUTCOME_FAILED)),
        pending=Count("id", filter=Q(outcome=OUTCOME_PENDING)),
    )
    return {k: agg[k] or 0 for k in ("total", "success", "failed", "pending")}


def _annotate_rows(rows):
    for op in rows:
        op.service_label = SERVICE_LABELS.get(op.service, op.service)
        op.outcome_label = OUTCOME_LABELS.get(op.outcome, op.outcome)
        op.source_label = SOURCE_LABELS.get(op.source, op.source)
        op.user_name = op.user.get_username() if op.user_id else ""
        op.duration_text = (
            f"{op.duration_ms / 1000:.1f} ث" if op.duration_ms is not None else ""
        )
        op.show_attempts = (op.attempts or 0) > 1
    return rows


@management_required
def provider_operations(request):
    params = request.GET
    period, date_from, date_to = _resolve_period(params)
    start = _local_midnight(date_from)
    end = _local_midnight(date_to + timedelta(days=1))
    in_period = ProviderOperation.objects.filter(created_at__gte=start, created_at__lt=end)

    counts = {
        row["provider_key"]: row["n"]
        for row in in_period.values("provider_key").annotate(n=Count("id"))
    }
    provider = params.get("provider")
    if provider not in ADAPTERS:
        provider = next((key for key in ADAPTERS if counts.get(key)), "sky")
    providers = [
        {"key": key, "name": key, "count": counts.get(key, 0), "active": key == provider}
        for key in ADAPTERS
    ]

    qs = in_period.filter(provider_key=provider)
    service = params.get("service") or ""
    if service not in SERVICE_LABELS:
        service = ""
    outcome = params.get("outcome") or ""
    if outcome not in OUTCOME_LABELS:
        outcome = ""
    source = params.get("source") or ""
    if source not in SOURCE_LABELS:
        source = ""
    phone = (params.get("phone") or "").strip()
    if service:
        qs = qs.filter(service=service)
    if outcome:
        qs = qs.filter(outcome=outcome)
    if source:
        qs = qs.filter(source=source)
    if phone:
        qs = qs.filter(phone__icontains=phone)

    page_obj = paginate_request(
        request,
        qs.select_related("user").order_by("-created_at", "-id"),
        per_page=PAGE_SIZE,
    )
    page_obj.object_list = _annotate_rows(list(page_obj.object_list))

    ctx = {
        "title": "تقارير المزودين",
        "providers": providers,
        "provider": provider,
        "period": period,
        "period_choices": list(PERIOD_LABELS.items()),
        "date_from": date_from.isoformat() if period == "custom" else "",
        "date_to": date_to.isoformat() if period == "custom" else "",
        "service": service,
        "outcome": outcome,
        "source": source,
        "phone": phone,
        "service_choices": list(SERVICE_LABELS.items()),
        "outcome_choices": list(OUTCOME_LABELS.items()),
        "source_choices": list(SOURCE_LABELS.items()),
        "summary": _summary(qs),
        "per_service": _per_service(qs),
        "top_causes": _top_causes(qs),
        "page_obj": page_obj,
    }
    return render(request, "reports/provider_operations.html", ctx)
