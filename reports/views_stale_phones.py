"""Phone line statement (بيان الأرقام) — management report."""

from __future__ import annotations

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from accounts.models import UserProfile
from accounts.permissions import management_required
from core.pagination import paginate_request
from reports.forms import PhoneLineFullEditForm, PhoneLinesFilterForm
from reports.models import PhoneLineTracking
from reports.stale_phones import (
    STATUS_OVERDUE,
    dismiss_phone_line,
    filter_stale_rows,
    normalize_reference_key,
    phone_lines_aggregate,
    rename_phone_line,
    sort_stale_rows,
)
from sales.views._shared import is_htmx

_SORT_FIELDS = frozenset(
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


def _sort_params(request, *, default_sort: str = "last_activity"):
    sort = request.GET.get("sort") or default_sort
    order = (request.GET.get("order") or "desc").lower()
    if sort not in _SORT_FIELDS:
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
    """Preserve list filters/sort/page across edit/renew redirects."""
    raw = request.POST.get("next_query") or request.GET.get("next_query") or ""
    if raw.startswith("?"):
        raw = raw[1:]
    return raw


def _safe_list_redirect(request, *, query: str = "") -> str:
    base = reverse("reports:phone_lines_report")
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


def _apply_threshold_from_form(profile, filter_form) -> int:
    """Persist threshold from the filter form when provided; else keep profile."""
    days = int(profile.stale_phone_threshold_days or 85)
    if filter_form.is_valid():
        raw = filter_form.cleaned_data.get("threshold_days")
        if raw is not None:
            days = max(1, int(raw))
            if days != profile.stale_phone_threshold_days:
                profile.stale_phone_threshold_days = days
                profile.save(update_fields=["stale_phone_threshold_days"])
    return days


def _phone_lines_filter_cleaned(request, profile, filter_form, *, default_th: int) -> tuple[dict, int]:
    """Return ``(cleaned_filters, threshold_days)``.

    When the bound form is invalid (stale line-name choice, bad company id,
    …), still honor the safe GET params so one bad field cannot wipe the
    rest of the filters — that looked like “filters stopped working”.
    """
    if filter_form.is_valid():
        days = _apply_threshold_from_form(profile, filter_form)
        return filter_form.cleaned_data, days

    days = default_th
    raw_th = request.GET.get("threshold_days")
    if raw_th not in (None, ""):
        try:
            days = max(1, min(3650, int(raw_th)))
        except (TypeError, ValueError):
            pass

    status = (request.GET.get("status") or "").strip().lower()
    allowed_status = {c[0] for c in filter_form.fields["status"].choices if c[0]}
    sim_type = (request.GET.get("sim_type") or "").strip().lower()
    allowed_sim = {c[0] for c in filter_form.fields["sim_type"].choices if c[0]}
    line_name = (request.GET.get("line_name") or "").strip().lower()
    allowed_lines = {c[0] for c in filter_form.fields["line_name"].choices if c[0]}
    cleaned: dict = {}
    if status in allowed_status:
        cleaned["status"] = status
    if sim_type in allowed_sim:
        cleaned["sim_type"] = sim_type
    if line_name in allowed_lines:
        cleaned["line_name"] = line_name
    company_raw = request.GET.get("company")
    if company_raw not in (None, ""):
        try:
            company = filter_form.fields["company"].queryset.get(pk=int(company_raw))
            cleaned["company"] = company
        except (TypeError, ValueError, filter_form.fields["company"].queryset.model.DoesNotExist):
            pass
    return cleaned, days


@management_required
def phone_lines_report(request):
    """Full inventory statement of every phone/reference (بيان الأرقام)."""
    profile = _management_profile(request)
    default_th = int(profile.stale_phone_threshold_days or 85)
    filter_form = PhoneLinesFilterForm(
        request.GET or None, default_threshold=default_th
    )
    cleaned, days = _phone_lines_filter_cleaned(
        request, profile, filter_form, default_th=default_th
    )

    rows = phone_lines_aggregate(threshold_days=days, due_soon_hours=24)
    rows = filter_stale_rows(
        rows,
        cleaned=cleaned,
        q=request.GET.get("q", ""),
    )
    sort, order = _sort_params(request, default_sort="last_activity")
    rows = sort_stale_rows(rows, sort=sort, order=order)
    page_obj = paginate_request(request, rows, allow_empty_first_page=True)
    list_query = request.GET.urlencode()
    ctx = {
        "title": _("Line statement"),
        "threshold_days": days,
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


def _row_for_ref(*, ref_key: str, threshold_days: int) -> dict | None:
    for row in phone_lines_aggregate(threshold_days=threshold_days):
        if row["ref_key"] == ref_key:
            return row
    return None


@management_required
def stale_phone_edit(request, ref_key: str):
    """Edit number / ICCID / notes. Changing the number resets disconnect countdown."""
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
    back_url = _safe_list_redirect(request, query=next_query)

    if request.method == "POST":
        form = PhoneLineFullEditForm(request.POST)
        if form.is_valid():
            try:
                rename_phone_line(
                    old_key=key,
                    new_reference=form.cleaned_data["reference_number"],
                    sim_identifier=form.cleaned_data.get("sim_identifier") or "",
                    notes=form.cleaned_data.get("notes") or "",
                    reset_countdown=True,
                )
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request,
                    _(
                        "Line updated. Disconnect countdown restarted from today "
                        "(virtual 1-day renew)."
                    ),
                )
                return redirect(back_url)
        else:
            messages.error(request, _("Please correct the errors below."))
    else:
        form = PhoneLineFullEditForm(
            initial={
                "reference_number": display,
                "sim_identifier": (meta.sim_identifier if meta else "")
                or row.get("sim_identifier")
                or "",
                "notes": (meta.notes if meta else "") or row.get("notes") or "",
            }
        )

    return render(
        request,
        "reports/stale_phone_edit.html",
        {
            "title": _("Edit line"),
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
    """Inline number + ICCID + notes save from the list (HTMX or normal POST)."""
    profile = _management_profile(request)
    key = normalize_reference_key(ref_key)
    if not key:
        raise Http404
    days = profile.stale_phone_threshold_days
    row = _row_for_ref(ref_key=key, threshold_days=days)
    if not row:
        raise Http404

    form = PhoneLineFullEditForm(request.POST)
    next_query = _list_query_from_request(request)
    list_url = _safe_list_redirect(request, query=next_query)

    def _error_response(*, status: int = 400):
        if is_htmx(request):
            return render(
                request,
                "reports/partials/stale_phone_sim_cell.html",
                {"row": row, "list_query": next_query, "form": form, "saved": False},
                status=status,
            )
        messages.error(request, _("Could not save."))
        return redirect(list_url)

    if not form.is_valid():
        return _error_response()

    new_display = form.cleaned_data["reference_number"]
    new_key = normalize_reference_key(new_display)
    old_display = (row.get("display_reference") or "").strip()
    number_changed = new_key != key or new_display != old_display
    try:
        rename_phone_line(
            old_key=key,
            new_reference=new_display,
            sim_identifier=form.cleaned_data.get("sim_identifier") or "",
            notes=form.cleaned_data.get("notes") or "",
            reset_countdown=number_changed,
        )
    except ValueError as exc:
        form.add_error("reference_number", str(exc))
        return _error_response()

    if number_changed:
        messages.success(
            request,
            _(
                "Line updated. Disconnect countdown restarted from today "
                "(virtual 1-day renew)."
            ),
        )
        if is_htmx(request):
            resp = HttpResponse(status=204)
            resp["HX-Redirect"] = list_url
            return resp
        return redirect(list_url)

    # Same number: refresh the cell in place (ICCID / notes only).
    row = _row_for_ref(ref_key=new_key, threshold_days=days) or row
    row["sim_identifier"] = (form.cleaned_data.get("sim_identifier") or "").strip()
    row["notes"] = (form.cleaned_data.get("notes") or "").strip()
    row["display_reference"] = new_display
    if is_htmx(request):
        return render(
            request,
            "reports/partials/stale_phone_sim_cell.html",
            {"row": row, "list_query": next_query, "saved": True},
        )
    messages.success(request, _("Saved."))
    return redirect(list_url)


@management_required
@require_POST
def phone_line_delete(request, ref_key: str):
    """Remove the number from the statement inventory (dismiss)."""
    profile = _management_profile(request)
    key = normalize_reference_key(ref_key)
    if not key:
        raise Http404
    days = profile.stale_phone_threshold_days
    row = _row_for_ref(ref_key=key, threshold_days=days)
    if not row:
        raise Http404
    dismiss_phone_line(
        ref_key=key, display_reference=row.get("display_reference") or key
    )
    messages.success(
        request,
        _("Number removed from the line statement. Sale history is kept."),
    )
    next_query = _list_query_from_request(request)
    return redirect(_safe_list_redirect(request, query=next_query))


@management_required
@require_POST
def phone_line_renew(request, ref_key: str):
    """
    Virtual 1-day renew for an overdue line (no Sale created).

    Sets ``manual_reset_at = now`` so lifecycle math treats the line as charged
    for 1 day from now, clearing the past-threshold status for another period.
    """
    profile = _management_profile(request)
    key = normalize_reference_key(ref_key)
    if not key:
        raise Http404
    days = profile.stale_phone_threshold_days
    row = _row_for_ref(ref_key=key, threshold_days=days)
    if not row or row.get("status") != STATUS_OVERDUE:
        raise Http404

    now = timezone.now()
    PhoneLineTracking.objects.update_or_create(
        reference_key=key,
        defaults={
            "manual_reset_at": now,
            "last_display_reference": (row["display_reference"] or "")[:64],
        },
    )
    messages.success(
        request,
        _("Renewed as a 1-day charge — the line is no longer marked as left the agency."),
    )
    next_query = _list_query_from_request(request)
    return redirect(_safe_list_redirect(request, query=next_query))
