"""Versioned ``/api/v1/`` endpoints for internal systems and the online store.

Bearer-token only (no anonymous mode); every error is JSON with a stable
``error`` code. Business outcomes (invalid phone, cooldown, ...) are the
normal refresh payload with HTTP 200.
"""
from __future__ import annotations

import functools
import json

from django.core.exceptions import RequestDataTooBig
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt

from core.http_utils import get_client_ip
from core.ratelimit import allow, exceeded
from phone_refresh.models import RefreshSource
from phone_refresh.services.refresh_jobs import (
    submit_refresh,
    token_job_payload,
    unknown_job_payload,
)
from phone_refresh.token_auth import extract_bearer_token, resolve_token, touch_token

SCOPE_REFRESH = "refresh"
SUBMIT_LIMIT_PER_MINUTE = 60
# Polling (every 2 s = 30/min per job) gets its own, larger budget so a
# token with a few jobs in flight cannot starve its own submits.
STATUS_LIMIT_PER_MINUTE = 300
# Failed authentications per client IP (only failures count), checked before
# the token lookup so a guesser is cut off without hashing/querying.
AUTH_FAIL_LIMIT_PER_MINUTE = 30


def _error(code: str, status: int, **headers) -> JsonResponse:
    response = JsonResponse({"error": code}, status=status)
    for name, value in headers.items():
        response[name.replace("_", "-")] = value
    return response


def _unauthorized() -> JsonResponse:
    return _error("unauthorized", 401, WWW_Authenticate="Bearer")


def _rate_limited() -> JsonResponse:
    return _error("rate_limited", 429, Retry_After="60")


def method_only(method: str):
    """JSON 405 (with ``Allow``) unless the request uses ``method``."""

    def decorator(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.method != method:
                return _error("method_not_allowed", 405, Allow=method)
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def bearer_scope_required(scope: str):
    """Authenticate ``Authorization: Bearer`` and require ``scope``.

    Passes the resolved ``ApiToken`` to the view as ``request.api_token``.
    Failed authentications are throttled per client IP.
    """

    def decorator(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            ip = get_client_ip(request) or ""
            if exceeded("api-auth-fail", ip, AUTH_FAIL_LIMIT_PER_MINUTE):
                return _rate_limited()
            raw = extract_bearer_token(request)
            token = resolve_token(raw) if raw else None
            if token is None:
                allow("api-auth-fail", ip, AUTH_FAIL_LIMIT_PER_MINUTE, 60)
                return _unauthorized()
            if not token.has_scope(scope):
                return _error("insufficient_scope", 403)
            touch_token(token)
            request.api_token = token
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def _parse_phone(request) -> tuple[str | None, JsonResponse | None]:
    try:
        body = json.loads(request.body.decode("utf-8") or "null")
    except RequestDataTooBig:
        return None, _error("payload_too_large", 413)
    except (ValueError, UnicodeDecodeError):
        return None, _error("invalid_json", 400)
    if not isinstance(body, dict):
        return None, _error("invalid_json", 400)
    phone = body.get("phone")
    if not isinstance(phone, str) or not phone.strip():
        return None, _error("phone_required", 400)
    return phone.strip(), None


@csrf_exempt
@never_cache
@method_only("POST")
@bearer_scope_required(SCOPE_REFRESH)
def refresh_submit(request):
    token = request.api_token
    if not allow("api-token", str(token.pk), SUBMIT_LIMIT_PER_MINUTE, 60):
        return _rate_limited()
    phone, error = _parse_phone(request)
    if error is not None:
        return error

    ticket = submit_refresh(
        phone,
        ip=get_client_ip(request),
        source=RefreshSource.API,
        api_token_id=token.pk,
    )
    if ticket.job_id is None:
        # Answered without a job (validation, cooldown, service off, ...).
        return JsonResponse(ticket.payload)
    return JsonResponse({"job": ticket.job_id, "status": "processing"}, status=202)


@csrf_exempt
@never_cache
@method_only("GET")
@bearer_scope_required(SCOPE_REFRESH)
def refresh_status(request, job_id):
    token = request.api_token
    if not allow("api-token-status", str(token.pk), STATUS_LIMIT_PER_MINUTE, 60):
        return _rate_limited()
    payload = token_job_payload(str(job_id), token.pk)
    if payload is None:
        # Same answer for "not yours" and "does not exist".
        return JsonResponse(unknown_job_payload(), status=404)
    return JsonResponse(payload)


@csrf_exempt
@never_cache
def not_found(request, *args, **kwargs):
    """Catch-all so every ``/api/v1/`` miss is JSON, not an HTML 404."""
    return _error("not_found", 404)
