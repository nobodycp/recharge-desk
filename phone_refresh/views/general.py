"""Views for the General settings tab + the internal-test admin tool."""
from __future__ import annotations

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.permissions import management_required
from phone_refresh.forms import InternalTestForm, SystemSettingsForm
from phone_refresh.models import RefreshStatus, SystemSettings
from phone_refresh.services.refresh_jobs import run_internal_test


GENERAL_TAB_ID = "general"


def _settings_url(active_tab: str) -> str:
    return f"{reverse('phone_refresh:settings')}?tab={active_tab}"


@management_required
@require_POST
def settings_general_save(request):
    """POST-only: persist the SystemSettings singleton from the general tab."""
    instance = SystemSettings.get()
    form = SystemSettingsForm(request.POST, instance=instance)
    if form.is_valid():
        form.save()
        messages.success(request, "تم حفظ الإعدادات.")
    else:
        # Surface validation errors as a single flash; the tab will re-render
        # the form with field-level errors via the session-stored bound form.
        first_error = next(iter(form.errors.values()), ["تعذّر حفظ الإعدادات."])[0]
        messages.error(request, first_error)
    return redirect(_settings_url(GENERAL_TAB_ID))


@management_required
@require_POST
def settings_internal_test(request):
    """Admin-only refresh executor that bypasses cooldown and service-off.

    Returns JSON so the General-tab page can render the result inline
    without a full page reload.
    """
    form = InternalTestForm(request.POST)
    if not form.is_valid():
        first_error = next(iter(form.errors.values()), ["خطأ في المدخلات."])[0]
        return JsonResponse(
            {
                "ok": False,
                "error": first_error,
            },
            status=400,
        )

    phone = form.cleaned_data["phone"]
    outcome = run_internal_test(phone)
    payload = outcome.payload
    diag = outcome.diagnostics
    status = payload.get("status")
    status_row = RefreshStatus.objects.filter(code=status).first()

    return JsonResponse(
        {
            "ok": True,
            "phone": phone,
            "status": status,
            "status_label": status_row.label if status_row else status,
            "provider": diag.get("provider"),
            "matched_rule_id": diag.get("matched_rule_id"),
            "message": payload.get("message") or {"title": "", "body": ""},
            "raw_status_code": diag.get("raw_status_code"),
            "raw_excerpt": outcome.raw_excerpt,
            "error": diag.get("error"),
            "last_refresh_at": payload.get("last_refresh_at"),
            "seconds_since_last_refresh": payload.get("seconds_since_last_refresh"),
        }
    )
