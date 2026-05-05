"""Lines idle for N days — management report."""

from __future__ import annotations

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from accounts.models import UserProfile
from accounts.permissions import management_required
from core.pagination import paginate_request
from reports.forms import (
    StalePhoneLineEditForm,
    StalePhonesFilterForm,
    StalePhoneThresholdDaysForm,
)
from reports.models import PhoneLineTracking
from reports.stale_phones import (
    filter_stale_rows,
    normalize_reference_key,
    sort_stale_rows,
    stale_reference_aggregate,
)
from sales.views._shared import is_htmx

_STALE_SORT_FIELDS = frozenset(
    {"ref", "idle_days", "last_activity", "customer", "company", "product", "sim"}
)


def _stale_sort_params(request):
    sort = request.GET.get("sort") or "idle_days"
    order = (request.GET.get("order") or "desc").lower()
    if sort not in _STALE_SORT_FIELDS:
        sort = "idle_days"
    if order not in ("asc", "desc"):
        order = "desc"
    return sort, order


def _management_profile(request):
    try:
        return request.user.profile
    except UserProfile.DoesNotExist as exc:  # pragma: no cover
        raise Http404 from exc


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
    ctx = {
        "title": _("Lines for disconnect"),
        "threshold_form": th_form,
        "threshold_days": days,
        "idle_threshold_details_open": th_form.is_bound and not th_form.is_valid(),
        "filter_form": filter_form,
        "page_obj": page_obj,
        "sort": sort,
        "order": order,
    }
    if is_htmx(request):
        return render(
            request,
            "reports/partials/stale_phones_report_results.html",
            ctx,
        )
    return render(request, "reports/stale_phones_report.html", ctx)


def _ref_in_stale_list(*, ref_key: str, threshold_days: int) -> bool:
    return any(
        r["ref_key"] == ref_key
        for r in stale_reference_aggregate(threshold_days=threshold_days)
    )


@management_required
def stale_phone_edit(request, ref_key: str):
    profile = _management_profile(request)
    key = normalize_reference_key(ref_key)
    if not key:
        raise Http404
    days = profile.stale_phone_threshold_days
    if not _ref_in_stale_list(ref_key=key, threshold_days=days):
        raise Http404

    display = next(
        (
            r["display_reference"]
            for r in stale_reference_aggregate(threshold_days=days)
            if r["ref_key"] == key
        ),
        key,
    )
    meta = PhoneLineTracking.objects.filter(
        reference_key=key, is_dismissed=False
    ).first()

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
            return redirect("reports:stale_phones_report")
        messages.error(request, _("Please correct the errors below."))
    else:
        form = StalePhoneLineEditForm(
            initial={"sim_identifier": (meta.sim_identifier if meta else "")}
        )

    return render(
        request,
        "reports/stale_phone_edit.html",
        {
            "title": _("Edit line"),
            "form": form,
            "ref_key": key,
            "display_reference": display,
            "back_url": reverse("reports:stale_phones_report"),
        },
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
    return redirect("reports:stale_phones_report")
