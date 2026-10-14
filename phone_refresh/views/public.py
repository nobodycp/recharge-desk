"""Public-facing refresh page + JSON API endpoint (no auth, rate-limited)."""
from __future__ import annotations

import hashlib
import json

from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils import translation
from django.views.decorators.cache import never_cache
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
from core.ratelimit import allow
from phone_refresh.services.refresh_jobs import (
    job_payload,
    submit_refresh,
    wait_for_job,
)
from phone_refresh.services.refresh_service import status_payload

SYNC_WAIT_SECONDS = 45
# Status polling (every 2 s = 30/min per job): one counter per ip+job so
# customers sharing an IP (CGNAT) don't starve each other, plus an IP-wide
# ceiling against job-id enumeration.
STATUS_LIMIT_PER_JOB_PER_MINUTE = 60
STATUS_LIMIT_PER_IP_PER_MINUTE = 600

_RATE_LIMITED_BODY = {
    "status": "error",
    "message": {
        "title": "محاولات كثيرة",
        "body": "لقد أرسلت طلبات كثيرة بسرعة. يرجى الانتظار قليلًا.",
    },
}


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


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes")


def _parse_refresh_payload(
    request,
) -> tuple[str | None, str, str, bool, HttpResponse | None]:
    """Extract ``phone``, ``client_hint``, honeypot and ``async`` from the POST body.

    Returns ``(phone, client_hint, honeypot, want_async, error_response)``.
    When ``error_response`` is not ``None`` the caller should return it
    immediately (malformed JSON).
    """
    content_type = (request.content_type or "").lower()
    phone: str | None = None
    client_hint = ""
    honeypot = ""
    want_async = False
    if "application/json" in content_type:
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except ValueError:
            return None, "", "", False, HttpResponseBadRequest("invalid JSON body")
        if isinstance(payload, dict):
            phone = payload.get("phone_number") or payload.get("phone")
            client_hint = str(payload.get("client") or "").strip().lower()
            honeypot = str(payload.get("website") or "").strip()
            want_async = _truthy(payload.get("async"))
    else:
        phone = request.POST.get("phone_number") or request.POST.get("phone")
        client_hint = (request.POST.get("client") or "").strip().lower()
        honeypot = (request.POST.get("website") or "").strip()
        want_async = _truthy(request.POST.get("async"))
    return phone, client_hint, honeypot, want_async, None


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
    with translation.override(lang):
        public_api_url = reverse("phone_refresh:public_api")
        # The JS swaps the all-zero placeholder for the real job id.
        public_status_url = reverse(
            "phone_refresh:public_status",
            args=["00000000-0000-0000-0000-000000000000"],
        )
    return render(
        request,
        "phone_refresh/public_refresh.html",
        {
            "whatsapp_url": site_settings.whatsapp_url,
            "facebook_url": site_settings.facebook_url,
            "public_api_token": public_api_token,
            "public_api_url": public_api_url,
            "public_status_url": public_status_url,
            "refresh_wait_seconds": SystemSettings.get().refresh_wait_seconds,
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
    * ``rate_limit_per_minute`` / ``rate_limit_per_hour`` → fixed-window
      counters per client IP (shared across workers via Redis).
    * ``"async": true`` → answer ``202 {"status": "processing", "job": id}``
      and let the caller poll :func:`public_refresh_status`; otherwise
      wait up to 45 s for the final payload (previous behaviour).
    * ``allowed_origins`` → simple Origin header allowlist (empty =
      allow any).
    """
    ip = get_client_ip(request) or ""
    api_settings = ApiSettings.get()

    if not _check_origin(request, api_settings):
        return JsonResponse({"error": "origin_not_allowed"}, status=403)

    if not (
        allow("refresh-min", ip, api_settings.rate_limit_per_minute, 60)
        and allow("refresh-hour", ip, api_settings.rate_limit_per_hour, 3600)
    ):
        return JsonResponse(_RATE_LIMITED_BODY, status=429)

    phone, client_hint, honeypot, want_async, parse_error = _parse_refresh_payload(
        request
    )
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
    if client_hint == RefreshSource.WEB:
        source = RefreshSource.WEB
    elif client_hint == RefreshSource.SMS:
        source = RefreshSource.SMS
    else:
        source = RefreshSource.API
    ticket = submit_refresh(str(phone), ip=ip or None, source=source)

    status_code = 200
    if ticket.job_id is None:
        # Answered without a job (validation, cooldown, service off, or a
        # failed enqueue → "busy"): always a plain 200 with that payload.
        payload = ticket.payload
    elif want_async:
        status_code = 202
        payload = {"status": "processing", "job": ticket.job_id}
    else:
        payload = wait_for_job(ticket.job_id, SYNC_WAIT_SECONDS)
        if payload.get("status") == "processing" and not payload.get("final"):
            # Still running after the sync wait: answer like any other
            # final payload (status + message) instead of leaking the
            # poll-only ``{"status": "processing", "job": id}`` shape.
            payload = status_payload("processing", final=True)

    if token is not None:
        # Update audit field after the response logic settled so a slow
        # save can't delay/error out the actual refresh result.
        ApiToken.objects.filter(pk=token.pk).update(last_used_at=timezone.now())

    return JsonResponse(payload, status=status_code)


@never_cache
@require_GET
def public_refresh_status(request, job_id):
    """Poll a refresh job started with ``async: true``.

    Returns exactly what :func:`job_payload` returns (customer-safe). Job
    ids are random UUIDs, so unknown ids simply get the generic error
    payload instead of a 404.

    Rate limits: ``refresh-status`` per ``ip:job_id`` and ``refresh-status-ip``
    per IP, so shared-IP customers polling different jobs don't collide.
    """
    ip = get_client_ip(request) or ""
    if not (
        allow("refresh-status-ip", ip, STATUS_LIMIT_PER_IP_PER_MINUTE, 60)
        and allow(
            "refresh-status", f"{ip}:{job_id}", STATUS_LIMIT_PER_JOB_PER_MINUTE, 60
        )
    ):
        return JsonResponse(_RATE_LIMITED_BODY, status=429)
    return JsonResponse(job_payload(str(job_id)))
