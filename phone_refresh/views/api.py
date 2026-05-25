"""Management UI for the public refresh API: settings, tokens, live test.

Lives at ``/management/phone-refresh/api/`` with three internal tabs
(``settings`` / ``tokens`` / ``test``) that follow the same layout as
the existing settings page. The live-test tab dispatches the real
:func:`public_refresh_api` view in-process so the panel exercises the
exact same code path an external client hits.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time

from django.contrib import messages
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.permissions import management_required
from phone_refresh.forms import ApiSettingsForm, ApiTokenForm
from phone_refresh.models import ApiSettings, ApiToken, RefreshStatus
from phone_refresh.views.public import public_refresh_api

API_SETTINGS_TAB = "settings"
API_TOKENS_TAB = "tokens"
API_TEST_TAB = "test"
API_VALID_TABS = {API_SETTINGS_TAB, API_TOKENS_TAB, API_TEST_TAB}

PHONE_RE = re.compile(r"^05\d{8}$")


def _api_url(active_tab: str) -> str:
    return f"{reverse('phone_refresh:api_index')}?tab={active_tab}"


# ---------------------------------------------------------------------------
# Index (tabbed page)
# ---------------------------------------------------------------------------


@management_required
def api_index(request):
    active_tab = request.GET.get("tab") or API_SETTINGS_TAB
    if active_tab not in API_VALID_TABS:
        active_tab = API_SETTINGS_TAB

    api_settings = ApiSettings.get()
    settings_form = ApiSettingsForm(instance=api_settings)

    tokens = list(ApiToken.objects.select_related("created_by").all())

    statuses = list(RefreshStatus.objects.all())

    api_endpoint = request.build_absolute_uri(reverse("phone_refresh:public_api"))

    ctx = {
        "title": "إدارة الواجهة البرمجية (API)",
        "active_tab": active_tab,
        "settings_tab_id": API_SETTINGS_TAB,
        "tokens_tab_id": API_TOKENS_TAB,
        "test_tab_id": API_TEST_TAB,
        "api_settings": api_settings,
        "settings_form": settings_form,
        "tokens": tokens,
        "refresh_statuses": statuses,
        "api_endpoint": api_endpoint,
    }
    return render(request, "phone_refresh/api_index.html", ctx)


# ---------------------------------------------------------------------------
# Tab 1 — settings save
# ---------------------------------------------------------------------------


@management_required
@require_POST
def api_settings_save(request):
    instance = ApiSettings.get()
    form = ApiSettingsForm(request.POST, instance=instance)
    if form.is_valid():
        form.save()
        messages.success(request, "تم حفظ إعدادات API.")
    else:
        first_error = next(iter(form.errors.values()), ["تعذّر حفظ الإعدادات."])[0]
        messages.error(request, first_error)
    return redirect(_api_url(API_SETTINGS_TAB))


# ---------------------------------------------------------------------------
# Tab 2 — token CRUD
# ---------------------------------------------------------------------------


@management_required
def api_token_create(request):
    form = ApiTokenForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        raw_token = secrets.token_urlsafe(48)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        token = form.save(commit=False)
        token.token_hash = token_hash
        token.prefix = raw_token[:8]
        if request.user.is_authenticated:
            token.created_by = request.user
        token.save()

        return render(
            request,
            "phone_refresh/api_token_created.html",
            {
                "title": "تم إنشاء التوكن",
                "token": token,
                "raw_token": raw_token,
            },
        )

    return render(
        request,
        "phone_refresh/api_token_form.html",
        {
            "form": form,
            "title": "توكن جديد",
        },
    )


@management_required
@require_POST
def api_token_revoke(request, pk: int):
    token = get_object_or_404(ApiToken, pk=pk)
    if token.revoked_at is None:
        token.revoked_at = timezone.now()
        token.save(update_fields=["revoked_at"])
        messages.success(request, "تم إلغاء التوكن.")
    else:
        messages.info(request, "التوكن مُلغى مسبقاً.")
    return redirect(_api_url(API_TOKENS_TAB))


@management_required
@require_POST
def api_token_delete(request, pk: int):
    token = get_object_or_404(ApiToken, pk=pk)
    if token.revoked_at is None:
        # Defensive: only allow physical deletion of already-revoked rows
        # so we keep an audit trail for active tokens.
        messages.error(request, "يجب إلغاء التوكن قبل حذفه.")
        return redirect(_api_url(API_TOKENS_TAB))
    token.delete()
    messages.success(request, "تم حذف التوكن.")
    return redirect(_api_url(API_TOKENS_TAB))


# ---------------------------------------------------------------------------
# Tab 3 — live test
# ---------------------------------------------------------------------------


@management_required
@require_POST
def api_live_test(request):
    """Internal proxy that dispatches the real :func:`public_refresh_api`.

    Builds a synthetic ``RequestFactory`` POST so the live-test panel
    exercises the exact same code path (rate-limiter, token check,
    refresh service) an external curl would hit. Returns a small JSON
    envelope with the upstream status code, response body and the
    server-side roundtrip duration so the page can render it inline.
    """
    if request.content_type and "application/json" in request.content_type.lower():
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except ValueError:
            return JsonResponse({"ok": False, "error": "invalid_json"}, status=400)
    else:
        payload = {
            "phone": request.POST.get("phone", ""),
            "token": request.POST.get("token", ""),
        }

    phone = (payload.get("phone") or "").strip()
    raw_token = (payload.get("token") or "").strip()

    if not PHONE_RE.match(phone):
        return JsonResponse(
            {"ok": False, "error": "الرقم يجب أن يبدأ بـ 05 ويتكوّن من 10 أرقام."},
            status=400,
        )

    factory = RequestFactory()
    headers: dict[str, str] = {}
    if raw_token:
        headers["HTTP_AUTHORIZATION"] = f"Bearer {raw_token}"
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        headers["HTTP_X_FORWARDED_FOR"] = forwarded
    remote = request.META.get("REMOTE_ADDR")
    if remote:
        headers["REMOTE_ADDR"] = remote

    inner_request = factory.post(
        reverse("phone_refresh:public_api"),
        data=json.dumps({"phone_number": phone}),
        content_type="application/json",
        **headers,
    )

    started = time.monotonic()
    response = public_refresh_api(inner_request)
    duration_ms = int((time.monotonic() - started) * 1000)

    body_text = response.content.decode("utf-8", errors="replace")
    parsed_body: object
    try:
        parsed_body = json.loads(body_text)
    except ValueError:
        parsed_body = body_text

    return JsonResponse(
        {
            "ok": True,
            "http_status": response.status_code,
            "duration_ms": duration_ms,
            "token_used": bool(raw_token),
            "response": parsed_body,
        }
    )
