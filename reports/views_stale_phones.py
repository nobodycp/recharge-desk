"""Phone line statement (بيان الأرقام) — management report."""

from __future__ import annotations

import json

from django.contrib import messages
from django.http import Http404, HttpResponse, JsonResponse, QueryDict
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from accounts.models import UserProfile
from accounts.permissions import management_required
from core.pagination import paginate_request
from reports.forms import PhoneLineFullEditForm, PhoneLinesFilterForm
from reports.iccid_fetch import (
    active_job_for_user,
    job_status,
    start_iccid_fetch_job,
)
from reports.models import PhoneLineTracking
from reports.stale_phones import (
    STATUS_OVERDUE,
    dismiss_phone_line,
    dismiss_phone_lines,
    filter_stale_rows,
    normalize_ref_keys,
    normalize_reference_key,
    phone_line_row,
    phone_lines_aggregate,
    rename_phone_line,
    sort_stale_rows,
)
from sales.views._shared import is_htmx


def _with_htmx_toast(response: HttpResponse, message: str, *, level: str = "success") -> HttpResponse:
    """Attach a client toast via HX-Trigger (see static/js/data-ui.js ``rdToast``)."""
    response["HX-Trigger"] = json.dumps(
        {"rdToast": {"message": str(message), "level": level}}
    )
    return response


def _with_htmx_toast_removed(response: HttpResponse, message: str, keys: list[str]) -> HttpResponse:
    """Toast plus a browser event that drops those rows without redrawing the grid."""
    response["HX-Trigger"] = json.dumps(
        {
            "rdToast": {"message": str(message), "level": "success"},
            "phoneLinesRemoved": {"keys": keys},
        }
    )
    return response

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


def _phone_lines_list_context(request, profile, *, get_params=None) -> dict:
    """Build the list/report context from ``get_params`` (defaults to request.GET)."""
    get = get_params if get_params is not None else request.GET
    original_get = request.GET
    request.GET = get
    try:
        default_th = int(profile.stale_phone_threshold_days or 85)
        filter_form = PhoneLinesFilterForm(
            get or None, default_threshold=default_th
        )
        cleaned, days = _phone_lines_filter_cleaned(
            request, profile, filter_form, default_th=default_th
        )
        rows = phone_lines_aggregate(threshold_days=days, due_soon_hours=24)
        rows = filter_stale_rows(
            rows,
            cleaned=cleaned,
            q=get.get("q", ""),
        )
        sort, order = _sort_params(request, default_sort="last_activity")
        rows = sort_stale_rows(rows, sort=sort, order=order)
        page_obj = paginate_request(request, rows, allow_empty_first_page=True)
        list_query = get.urlencode()
        return {
            "title": _("Line statement"),
            "threshold_days": days,
            "filter_form": filter_form,
            "page_obj": page_obj,
            "sort": sort,
            "order": order,
            "list_query": list_query,
            "iccid_fetch_job": active_job_for_user(request.user.pk),
        }
    finally:
        request.GET = original_get


def _htmx_phone_lines_results(
    request,
    profile,
    *,
    list_query: str,
    toast: str,
    level: str = "success",
    extra_trigger: dict | None = None,
) -> HttpResponse:
    """Re-render only the results grid after an in-list action + toast."""
    ctx = _phone_lines_list_context(
        request, profile, get_params=QueryDict(list_query or "")
    )
    resp = render(
        request,
        "reports/partials/phone_lines_report_results.html",
        ctx,
    )
    payload = {"rdToast": {"message": str(toast), "level": level}}
    if extra_trigger:
        payload.update(extra_trigger)
    resp["HX-Trigger"] = json.dumps(payload)
    return resp


@management_required
def phone_lines_report(request):
    """Full inventory statement of every phone/reference (بيان الأرقام)."""
    profile = _management_profile(request)
    ctx = _phone_lines_list_context(request, profile)
    if is_htmx(request):
        return render(
            request,
            "reports/partials/phone_lines_report_results.html",
            ctx,
        )
    return render(request, "reports/phone_lines_report.html", ctx)


@management_required
@require_GET
def phone_lines_iccid_status(request):
    """JSON progress for a background ICCID fetch job."""
    job_id = (request.GET.get("job") or "").strip()
    status = job_status(job_id) if job_id else active_job_for_user(request.user.pk)
    if status is None:
        return JsonResponse({"status": "missing", "running": False}, status=404)
    if str(status.get("user_id") or "") != str(request.user.pk):
        return JsonResponse({"status": "forbidden", "running": False}, status=403)
    return JsonResponse(status)


def _row_for_ref(*, ref_key: str, threshold_days: int) -> dict | None:
    return phone_line_row(ref_key=ref_key, threshold_days=threshold_days)


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
    """Inline number + ICCID + notes save from the list (HTMX refreshes the grid)."""
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

    def _error_response(message: str | None = None):
        err = message or _("Could not save.")
        if is_htmx(request):
            # Leave the grid as-is; toast explains the failure.
            resp = HttpResponse(status=200)
            resp["HX-Reswap"] = "none"
            return _with_htmx_toast(resp, err, level="error")
        messages.error(request, err)
        return redirect(list_url)

    if not form.is_valid():
        first_err = next(iter(form.errors.values()), [None])[0]
        return _error_response(str(first_err) if first_err else None)

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
        return _error_response(str(exc))

    if number_changed:
        ok_msg = _(
            "Line updated. Disconnect countdown restarted from today "
            "(virtual 1-day renew)."
        )
    else:
        ok_msg = _("Saved.")

    if is_htmx(request):
        return _htmx_phone_lines_results(
            request, profile, list_query=next_query, toast=ok_msg, level="success"
        )
    messages.success(request, ok_msg)
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
    ok_msg = _("Number removed from the line statement. Sale history is kept.")
    next_query = _list_query_from_request(request)
    if is_htmx(request):
        # Drop the row in the browser. Rebuilding the whole statement was the wait.
        resp = HttpResponse(status=200)
        resp["HX-Reswap"] = "none"
        return _with_htmx_toast_removed(resp, ok_msg, [key])
    messages.success(request, ok_msg)
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
    ok_msg = _(
        "Renewed as a 1-day charge — the line is no longer marked as left the agency."
    )
    next_query = _list_query_from_request(request)
    if is_htmx(request):
        return _htmx_phone_lines_results(
            request, profile, list_query=next_query, toast=ok_msg, level="success"
        )
    messages.success(request, ok_msg)
    return redirect(_safe_list_redirect(request, query=next_query))


def _posted_ref_keys(request) -> list[str]:
    raw = request.POST.getlist("refs")
    blob = request.POST.get("refs") or ""
    if blob and blob not in raw:
        raw = list(raw) + blob.splitlines()
    return normalize_ref_keys(raw)


def _display_numbers(keys, threshold_days: int) -> list[str]:
    """Phone numbers only, in the posted order. No names, status, or ICCID."""
    wanted = {key for key in keys if key}
    if not wanted:
        return []
    by_key = {
        row["ref_key"]: (row.get("display_reference") or "").strip()
        for row in phone_lines_aggregate(threshold_days=threshold_days)
        if row.get("ref_key") in wanted
    }
    return [by_key[key] for key in keys if by_key.get(key)]


def _filtered_ref_keys(request, profile, list_query: str) -> list[str]:
    """Every line matching the current report filters, not just this page."""
    get = QueryDict(list_query or "")
    original_get = request.GET
    request.GET = get
    try:
        default_th = int(profile.stale_phone_threshold_days or 85)
        filter_form = PhoneLinesFilterForm(get or None, default_threshold=default_th)
        cleaned, days = _phone_lines_filter_cleaned(
            request, profile, filter_form, default_th=default_th
        )
        rows = phone_lines_aggregate(threshold_days=days, due_soon_hours=24)
        rows = filter_stale_rows(rows, cleaned=cleaned, q=get.get("q", ""))
        return [row["ref_key"] for row in rows if row.get("ref_key")]
    finally:
        request.GET = original_get


@management_required
@require_POST
def phone_lines_bulk(request):
    """Delete, renew, fetch ICCID, or copy numbers for checked lines or the filter."""
    profile = _management_profile(request)
    action = (request.POST.get("action") or "").strip()
    next_query = _list_query_from_request(request)
    list_url = _safe_list_redirect(request, query=next_query)
    match_all = (request.POST.get("match") or "").strip() == "all"
    keys = (
        _filtered_ref_keys(request, profile, next_query)
        if match_all
        else _posted_ref_keys(request)
    )

    def _fail(message: str):
        if is_htmx(request):
            resp = HttpResponse(status=200)
            resp["HX-Reswap"] = "none"
            return _with_htmx_toast(resp, message, level="error")
        messages.error(request, message)
        return redirect(list_url)

    if action == "copy":
        numbers = _display_numbers(keys, profile.stale_phone_threshold_days) if keys else []
        if not numbers:
            return HttpResponse(status=400, content_type="text/plain; charset=utf-8")
        resp = HttpResponse(
            "\n".join(numbers),
            content_type="text/plain; charset=utf-8",
        )
        resp["HX-Reswap"] = "none"
        return resp

    if action not in ("delete", "renew", "fetch_iccid"):
        return _fail(_("Choose delete, renew, or fetch ICCID."))
    if not keys:
        return _fail(_("Select at least one line."))

    days = profile.stale_phone_threshold_days
    if action == "delete":
        removed = dismiss_phone_lines(keys)
        if not removed:
            return _fail(_("Select at least one line."))
        ok_msg = _(
            "Removed %(count)s numbers from the line statement. Sale history is kept."
        ) % {"count": len(removed)}
        if is_htmx(request) and match_all:
            return _htmx_phone_lines_results(
                request, profile, list_query=next_query, toast=ok_msg, level="success"
            )
        if is_htmx(request):
            resp = HttpResponse(status=200)
            resp["HX-Reswap"] = "none"
            return _with_htmx_toast_removed(resp, ok_msg, removed)
        messages.success(request, ok_msg)
        return redirect(list_url)

    if action == "fetch_iccid":
        try:
            job_id = start_iccid_fetch_job(keys, user_id=request.user.pk)
        except ValueError as exc:
            return _fail(str(exc))
        ok_msg = _(
            "ICCID fetch started in the background for %(count)s lines. "
            "Progress updates below."
        ) % {"count": len(keys)}
        trigger = {
            "iccidFetchStarted": {
                "job_id": job_id,
                "status_url": reverse("reports:phone_lines_iccid_status"),
            }
        }
        if is_htmx(request):
            return _htmx_phone_lines_results(
                request,
                profile,
                list_query=next_query,
                toast=ok_msg,
                level="success",
                extra_trigger=trigger,
            )
        messages.success(request, ok_msg)
        return redirect(list_url)

    renewed = 0
    now = timezone.now()
    by_key = {
        row["ref_key"]: row
        for row in phone_lines_aggregate(threshold_days=days)
        if row["ref_key"] in set(keys)
    }
    for key in keys:
        row = by_key.get(key)
        if not row or row.get("status") != STATUS_OVERDUE:
            continue
        PhoneLineTracking.objects.update_or_create(
            reference_key=key,
            defaults={
                "manual_reset_at": now,
                "last_display_reference": (row["display_reference"] or "")[:64],
            },
        )
        renewed += 1
    if not renewed:
        return _fail(_("None of the selected lines are overdue."))
    ok_msg = _("Renewed %(count)s overdue lines as a 1-day charge.") % {"count": renewed}
    if is_htmx(request):
        return _htmx_phone_lines_results(
            request, profile, list_query=next_query, toast=ok_msg, level="success"
        )
    messages.success(request, ok_msg)
    return redirect(list_url)
