"""Views for the General settings tab + the internal-test admin tool."""
from __future__ import annotations

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.permissions import management_required
from phone_refresh.forms import InternalTestForm, SystemSettingsForm
from phone_refresh.models import RefreshSource, SystemSettings
from phone_refresh.services.refresh_service import refresh_phone


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
    result = refresh_phone(
        phone,
        ip=None,
        internal_test=True,
        source=RefreshSource.INTERNAL_TEST,
    )

    return JsonResponse(
        {
            "ok": True,
            "phone": phone,
            "status": result.status.code,
            "status_label": result.status.label,
            "provider": result.provider,
            "matched_rule_id": result.matched_rule_id,
            "message": {
                "title": result.message_title,
                "body": result.message_body,
            },
            "raw_status_code": result.raw_status_code,
            "raw_excerpt": result.raw_excerpt,
            "error": result.error,
            "last_refresh_at": (
                result.last_refresh_at.isoformat()
                if result.last_refresh_at is not None
                else None
            ),
            "seconds_since_last_refresh": result.seconds_since_last_refresh,
        }
    )
