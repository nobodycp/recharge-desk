"""Lines for disconnect — management report."""

from __future__ import annotations

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from accounts.models import UserProfile
from accounts.permissions import management_required
from core.pagination import paginate_request
from reports.forms import (
    PhoneLinesFilterForm,
    StalePhoneLineEditForm,
    StalePhonesFilterForm,
    StalePhoneThresholdDaysForm,
)
from reports.models import PhoneLineTracking
from reports.stale_phones import (
    filter_stale_rows,
    normalize_reference_key,
    phone_lines_aggregate,
    sort_stale_rows,
    stale_reference_aggregate,
)
from sales.views._shared import is_htmx

_STALE_SORT_FIELDS = frozenset(
    {
        "ref",
        "idle_days",
        "days_past_end",
        "last_activity",
        "subscription_end",
        "customer",
        "company",
        "product",
        "line",
        "sim",
        "duration",
        "status",
    }
)


def _stale_sort_params(request, *, default_sort: str = "days_past_end"):
    sort = request.GET.get("sort") or default_sort
    order = (request.GET.get("order") or "desc").lower()
    if sort not in _STALE_SORT_FIELDS:
        sort = default_sort
    if order not in ("asc", "desc"):
        order = "desc"
    return sort, order


def _management_profile(request):
    try:
        return request.user.profile
    except UserProfile.DoesNotExist as exc:  # pragma: no cover
        raise Http404 from exc


def _list_query_from_request(request) -> str:
    """Preserve list filters/sort/page across edit/dismiss redirects."""
    raw = request.POST.get("next_query") or request.GET.get("next_query") or ""
    if raw.startswith("?"):
        raw = raw[1:]
    return raw


def _safe_list_redirect(
    request, *, query: str = "", default_name: str = "reports:stale_phones_report"
) -> str:
    base = reverse(default_name)
    q = (query or "").lstrip("?")
    if q:
        return f"{base}?{q}"
    referer = request.META.get("HTTP_REFERER") or ""
    if referer and url_has_allowed_host_and_scheme(
        referer,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return referer
    return base


def _redirect_name_from_request(request) -> str:
    """Prefer returning to the phone-lines statement when that was the referer."""
    referer = request.META.get("HTTP_REFERER") or ""
    try:
        statement = reverse("reports:phone_lines_report")
    except Exception:  # pragma: no cover
        return "reports:stale_phones_report"
    if statement and statement in referer:
        return "reports:phone_lines_report"
    return "reports:stale_phones_report"


@management_required
def stale_phones_report(request):
    profile = _management_profile(request)
    if request.method == "POST" and request.POST.get("save_stale_threshold"):
        th_form = StalePhoneThresholdDaysForm(request.POST)
        if th_form.is_valid():
            profile.stale_phone_threshold_days = th_form.cleaned_data[
                "stale_phone_threshold_days"
            ]
            profile.save(update_fields=["stale_phone_threshold_days"])
            messages.success(request, _("Threshold saved."))
            return redirect("reports:stale_phones_report")
        messages.error(request, _("Could not save threshold."))
    else:
        th_form = StalePhoneThresholdDaysForm(
            initial={"stale_phone_threshold_days": profile.stale_phone_threshold_days}
        )

    days = profile.stale_phone_threshold_days
    filter_form = StalePhonesFilterForm(request.GET or None)
    cleaned = filter_form.cleaned_data if filter_form.is_valid() else {}
    rows = stale_reference_aggregate(threshold_days=days)
    rows = filter_stale_rows(
        rows,
        cleaned=cleaned,
        q=request.GET.get("q", ""),
    )
    sort, order = _stale_sort_params(request)
    rows = sort_stale_rows(rows, sort=sort, order=order)
    page_obj = paginate_request(request, rows, allow_empty_first_page=True)
    list_query = request.GET.urlencode()
    ctx = {
        "title": _("Lines for disconnect"),
        "threshold_form": th_form,
        "threshold_days": days,
        "idle_threshold_details_open": th_form.is_bound and not th_form.is_valid(),
        "filter_form": filter_form,
        "page_obj": page_obj,
        "sort": sort,
        "order": order,
        "list_query": list_query,
    }
    if is_htmx(request):
        return render(
            request,
            "reports/partials/stale_phones_report_results.html",
            ctx,
        )
    return render(request, "reports/stale_phones_report.html", ctx)


@management_required
def phone_lines_report(request):
    """Full inventory statement of every phone/reference (بيان الأرقام)."""
    profile = _management_profile(request)
    if request.method == "POST" and request.POST.get("save_stale_threshold"):
        th_form = StalePhoneThresholdDaysForm(request.POST)
        if th_form.is_valid():
            profile.stale_phone_threshold_days = th_form.cleaned_data[
                "stale_phone_threshold_days"
            ]
            profile.save(update_fields=["stale_phone_threshold_days"])
            messages.success(request, _("Threshold saved."))
            return redirect("reports:phone_lines_report")
        messages.error(request, _("Could not save threshold."))
    else:
        th_form = StalePhoneThresholdDaysForm(
            initial={"stale_phone_threshold_days": profile.stale_phone_threshold_days}
        )

    days = profile.stale_phone_threshold_days
    filter_form = PhoneLinesFilterForm(request.GET or None)
    cleaned = filter_form.cleaned_data if filter_form.is_valid() else {}
    rows = phone_lines_aggregate(threshold_days=days, due_soon_hours=24)
    rows = filter_stale_rows(
        rows,
        cleaned=cleaned,
        q=request.GET.get("q", ""),
    )
    sort, order = _stale_sort_params(request, default_sort="last_activity")
    rows = sort_stale_rows(rows, sort=sort, order=order)
    page_obj = paginate_request(request, rows, allow_empty_first_page=True)
    list_query = request.GET.urlencode()
    ctx = {
        "title": _("Line statement"),
        "threshold_form": th_form,
        "threshold_days": days,
        "idle_threshold_details_open": th_form.is_bound and not th_form.is_valid(),
        "filter_form": filter_form,
        "page_obj": page_obj,
        "sort": sort,
        "order": order,
        "list_query": list_query,
    }
    if is_htmx(request):
        return render(
            request,
            "reports/partials/phone_lines_report_results.html",
            ctx,
        )
    return render(request, "reports/phone_lines_report.html", ctx)


def _ref_in_stale_list(*, ref_key: str, threshold_days: int) -> bool:
    return any(
        r["ref_key"] == ref_key
        for r in stale_reference_aggregate(threshold_days=threshold_days)
    )


def _row_for_ref(*, ref_key: str, threshold_days: int) -> dict | None:
    """Resolve a line from the full statement (active + overdue + eSIM)."""
    for row in phone_lines_aggregate(threshold_days=threshold_days):
        if row["ref_key"] == ref_key:
            return row
    return None


@management_required
def stale_phone_edit(request, ref_key: str):
    profile = _management_profile(request)
    key = normalize_reference_key(ref_key)
    if not key:
        raise Http404
    days = profile.stale_phone_threshold_days
    row = _row_for_ref(ref_key=key, threshold_days=days)
    if not row:
        raise Http404

    display = row["display_reference"]
    meta = PhoneLineTracking.objects.filter(reference_key=key).first()
    next_query = _list_query_from_request(request)
    back_url = _safe_list_redirect(
        request,
        query=next_query,
        default_name=_redirect_name_from_request(request),
    )

    if request.method == "POST":
        form = StalePhoneLineEditForm(request.POST)
        if form.is_valid():
            PhoneLineTracking.objects.update_or_create(
                reference_key=key,
                defaults={
                    "sim_identifier": (form.cleaned_data.get("sim_identifier") or "").strip(),
                    "is_dismissed": False,
                    "last_display_reference": (display or "")[:64],
                },
            )
            messages.success(request, _("Line updated."))
            return redirect(back_url)
        messages.error(request, _("Please correct the errors below."))
    else:
        form = StalePhoneLineEditForm(
            initial={"sim_identifier": (meta.sim_identifier if meta else "")}
        )

    return render(
        request,
        "reports/stale_phone_edit.html",
        {
            "title": _("Edit ICCID / SIM"),
            "form": form,
            "ref_key": key,
            "display_reference": display,
            "back_url": back_url,
            "next_query": next_query,
            "row": row,
        },
    )


@management_required
@require_POST
def stale_phone_sim_save(request, ref_key: str):
    """Inline ICCID save from the list (HTMX or normal POST)."""
    profile = _management_profile(request)
    key = normalize_reference_key(ref_key)
    if not key:
        raise Http404
    days = profile.stale_phone_threshold_days
    row = _row_for_ref(ref_key=key, threshold_days=days)
    if not row:
        raise Http404

    form = StalePhoneLineEditForm(request.POST)
    next_query = _list_query_from_request(request)
    if form.is_valid():
        sim = (form.cleaned_data.get("sim_identifier") or "").strip()
        PhoneLineTracking.objects.update_or_create(
            reference_key=key,
            defaults={
                "sim_identifier": sim,
                "is_dismissed": False,
                "last_display_reference": (row["display_reference"] or "")[:64],
            },
        )
        row["sim_identifier"] = sim
        if is_htmx(request):
            return render(
                request,
                "reports/partials/stale_phone_sim_cell.html",
                {"row": row, "list_query": next_query, "saved": True},
            )
        messages.success(request, _("ICCID saved."))
        return redirect(
            _safe_list_redirect(
                request,
                query=next_query,
                default_name=_redirect_name_from_request(request),
            )
        )

    if is_htmx(request):
        return render(
            request,
            "reports/partials/stale_phone_sim_cell.html",
            {"row": row, "list_query": next_query, "form": form, "saved": False},
            status=400,
        )
    messages.error(request, _("Could not save ICCID."))
    return redirect(
        _safe_list_redirect(
            request,
            query=next_query,
            default_name=_redirect_name_from_request(request),
        )
    )


@management_required
@require_POST
def stale_phone_dismiss(request, ref_key: str):
    profile = _management_profile(request)
    key = normalize_reference_key(ref_key)
    if not key:
        raise Http404
    days = profile.stale_phone_threshold_days
    if not _ref_in_stale_list(ref_key=key, threshold_days=days):
        raise Http404
    obj, _created = PhoneLineTracking.objects.get_or_create(reference_key=key)
    obj.is_dismissed = True
    obj.save(update_fields=["is_dismissed", "updated_at"])
    messages.success(request, _("Removed from this list."))
    next_query = _list_query_from_request(request)
    return redirect(_safe_list_redirect(request, query=next_query))
