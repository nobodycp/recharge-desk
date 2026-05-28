"""Public-facing refresh page + JSON API endpoint (no auth, rate-limited)."""
from __future__ import annotations

import hashlib
import json
import threading
import time

from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from phone_refresh.models import (
    ApiSettings,
    ApiToken,
    RefreshSource,
    SiteSettings,
    SystemSettings,
)
from core.http_utils import get_client_ip
from phone_refresh.services.refresh_service import refresh_phone

# --- Lightweight in-process rate limiter ------------------------------------
# Sliding-window cap of N requests per IP per window. Two windows now (per
# minute and per hour) since the limits are admin-configurable from the API
# settings tab. Good enough for a single-process dev server; for production
# behind multiple workers, swap for django-ratelimit or a Redis-backed
# token bucket.
_RATE_WINDOW_MINUTE = 60
_RATE_WINDOW_HOUR = 3600

_rate_lock = threading.Lock()
_rate_state_minute: dict[str, list[float]] = {}
_rate_state_hour: dict[str, list[float]] = {}


def _check_rate_limit(ip: str, limit_per_min: int, limit_per_hour: int) -> bool:
    """Sliding-window check against per-minute and per-hour caps.

    Records the request in both windows when both checks pass.
    A limit of ``0`` disables that window (admins can opt out).
    """
    if not ip:
        return True
    now = time.monotonic()
    cutoff_min = now - _RATE_WINDOW_MINUTE
    cutoff_hour = now - _RATE_WINDOW_HOUR
    with _rate_lock:
        minute_bucket = [t for t in _rate_state_minute.get(ip, []) if t >= cutoff_min]
        hour_bucket = [t for t in _rate_state_hour.get(ip, []) if t >= cutoff_hour]

        if limit_per_min and len(minute_bucket) >= limit_per_min:
            _rate_state_minute[ip] = minute_bucket
            _rate_state_hour[ip] = hour_bucket
            return False
        if limit_per_hour and len(hour_bucket) >= limit_per_hour:
            _rate_state_minute[ip] = minute_bucket
            _rate_state_hour[ip] = hour_bucket
            return False

        minute_bucket.append(now)
        hour_bucket.append(now)
        _rate_state_minute[ip] = minute_bucket
        _rate_state_hour[ip] = hour_bucket
    return True


def _check_origin(request, api_settings: ApiSettings) -> bool:
    """Validate the request Origin against the admin-configured allowlist.

    Empty list → allow all. Empty/missing Origin header is treated as
    same-origin (server-to-server clients) and always allowed.
    """
    allowed = api_settings.allowed_origins_list
    if not allowed:
        return True
    origin = (request.META.get("HTTP_ORIGIN") or "").strip()
    if not origin:
        return True
    return origin in allowed


def _extract_bearer_token(request) -> str | None:
    header = request.META.get("HTTP_AUTHORIZATION") or ""
    if not header.lower().startswith("bearer "):
        return None
    return header[7:].strip() or None


def _resolve_token(raw_token: str) -> ApiToken | None:
    digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    try:
        token = ApiToken.objects.get(token_hash=digest)
    except ApiToken.DoesNotExist:
        return None
    return token if token.is_active else None


def _parse_refresh_payload(request) -> tuple[str | None, str, str, HttpResponse | None]:
    """Extract ``phone``, ``client_hint``, and honeypot from the POST body.

    Returns a 4-tuple ``(phone, client_hint, honeypot, error_response)``.
    When ``error_response`` is not ``None`` the caller should return it
    immediately (malformed JSON).
    """
    content_type = (request.content_type or "").lower()
    phone: str | None = None
    client_hint = ""
    honeypot = ""
    if "application/json" in content_type:
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except ValueError:
            return None, "", "", HttpResponseBadRequest("invalid JSON body")
        if isinstance(payload, dict):
            phone = payload.get("phone_number") or payload.get("phone")
            client_hint = str(payload.get("client") or "").strip().lower()
            honeypot = str(payload.get("website") or "").strip()
    else:
        phone = request.POST.get("phone_number") or request.POST.get("phone")
        client_hint = (request.POST.get("client") or "").strip().lower()
        honeypot = (request.POST.get("website") or "").strip()
    return phone, client_hint, honeypot, None


def _token_required(api_settings: ApiSettings, client_hint: str) -> bool:
    """Return whether this request must carry a valid bearer token."""
    if not api_settings.require_token:
        return False
    if (
        api_settings.allow_anonymous_test_page
        and client_hint == RefreshSource.WEB
    ):
        return False
    return True


@require_GET
def public_refresh_page(request):
    """Standalone Arabic RTL refresh form (not part of the admin shell)."""
    from core.models import AppSettings

    site_settings = SiteSettings.get_solo()
    app_settings = AppSettings.load()
    public_api_token = ""
    token = site_settings.public_page_token
    if (
        token is not None
        and token.is_active
        and site_settings.public_page_token_raw
    ):
        public_api_token = site_settings.public_page_token_raw
    lang = app_settings.public_default_language or "ar"
    theme = app_settings.public_default_theme or "dark"
    return render(
        request,
        "phone_refresh/public_refresh.html",
        {
            "whatsapp_url": site_settings.whatsapp_url,
            "facebook_url": site_settings.facebook_url,
            "public_api_token": public_api_token,
            "public_api_url": "/phone-refresh/api/refresh/",
            "public_default_language": lang,
            "public_default_theme": theme,
            "public_html_dir": "rtl" if lang == "ar" else "ltr",
        },
    )


@csrf_exempt
@require_POST
def public_refresh_api(request):
    """JSON endpoint posted to by the public form (and external clients).

    Honors the API settings singleton:

    * ``require_token`` → enforce ``Authorization: Bearer <token>`` and
      bump the matching :class:`ApiToken`'s ``last_used_at``. When
      ``allow_anonymous_test_page`` is ON, requests from the bundled
      public form (``client=web``) skip the token gate (not recommended
      for production).
    * ``rate_limit_per_minute`` / ``rate_limit_per_hour`` → sliding
      window per client IP.
    * ``allowed_origins`` → simple Origin header allowlist (empty =
      allow any).
    """
    ip = get_client_ip(request) or ""
    api_settings = ApiSettings.get()

    if not _check_origin(request, api_settings):
        return JsonResponse({"error": "origin_not_allowed"}, status=403)

    if not _check_rate_limit(
        ip,
        api_settings.rate_limit_per_minute,
        api_settings.rate_limit_per_hour,
    ):
        return JsonResponse(
            {
                "status": "error",
                "message": {
                    "title": "محاولات كثيرة",
                    "body": "لقد أرسلت طلبات كثيرة بسرعة. يرجى الانتظار قليلًا.",
                },
            },
            status=429,
        )

    phone, client_hint, honeypot, parse_error = _parse_refresh_payload(request)
    if parse_error is not None:
        return parse_error

    token: ApiToken | None = None
    if _token_required(api_settings, client_hint):
        raw_token = _extract_bearer_token(request)
        if not raw_token:
            return JsonResponse({"error": "missing_token"}, status=401)
        token = _resolve_token(raw_token)
        if token is None:
            return JsonResponse({"error": "invalid_token"}, status=401)

    # Honeypot: legitimate browser users never see/fill the hidden
    # ``website`` field. If it's populated, short-circuit with a benign
    # error so bots don't get feedback that the trap exists.
    if honeypot:
        return JsonResponse(
            {
                "status": "error",
                "message": {"title": "خطأ", "body": "تعذّر إتمام العملية."},
            },
            status=400,
        )

    if not phone:
        return JsonResponse(
            {"status": "error", "message": {"title": "خطأ", "body": "رقم الهاتف مطلوب."}},
            status=400,
        )

    # Source resolution: explicit "web" hint from the bundled HTML form
    # wins; everything else (external curl/JSON clients, with or without a
    # bearer token) is recorded as "api". This keeps the two public
    # entry-points distinguishable in the reports view without adding a
    # second URL just for source tracking.
    source = (
        RefreshSource.WEB if client_hint == RefreshSource.WEB else RefreshSource.API
    )
    result = refresh_phone(str(phone), ip=ip or None, source=source)

    payload = {
        "status": result.status.code,
        "message": {"title": result.message_title, "body": result.message_body},
    }

    # Only expose last_refresh fields when the admin toggle is on. The
    # service already nulls them out when off, but we double-check here so
    # the response shape is unambiguous.
    if SystemSettings.get().show_last_refresh:
        payload["last_refresh_at"] = (
            result.last_refresh_at.isoformat()
            if result.last_refresh_at is not None
            else None
        )
        payload["seconds_since_last_refresh"] = result.seconds_since_last_refresh

    if token is not None:
        # Update audit field after the response logic settled so a slow
        # save can't delay/error out the actual refresh result.
        ApiToken.objects.filter(pk=token.pk).update(last_used_at=timezone.now())

    return JsonResponse(payload)
