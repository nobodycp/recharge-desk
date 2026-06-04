"""HTTP client for the configurable refresh API gateway.

The SMS service does not call the refresh engine in-process; it POSTs to
an admin-configured URL (default: the internal phone-refresh endpoint) so
the source can later be swapped to an external API from the GUI without a
code change. The response is parsed using configurable dotted paths.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import requests
from django.urls import reverse

from sms_gateway.models import SmsGatewaySettings

log = logging.getLogger(__name__)


@dataclass
class SmsRefreshOutcome:
    status_code: str
    title: str
    body: str
    http_status: int | None = None
    error: str | None = None


def _dotted_get(data, path: str):
    """Read ``data`` by a dotted path like ``message.body``; tolerant of None."""
    if not path:
        return None
    current = data
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _resolve_url(settings_obj: SmsGatewaySettings, *, request=None) -> str:
    url = (settings_obj.refresh_api_url or "").strip()
    if url:
        return url
    path = reverse("phone_refresh:public_api")
    if request is not None:
        return request.build_absolute_uri(path)
    # Fallback for non-request contexts (cron/tests): localhost loopback.
    return f"http://127.0.0.1:8000{path}"


def call_refresh_api(
    phone: str,
    *,
    settings_obj: SmsGatewaySettings | None = None,
    request=None,
) -> SmsRefreshOutcome:
    """Call the configured refresh API for ``phone`` and parse the reply."""
    s = settings_obj or SmsGatewaySettings.load()
    url = _resolve_url(s, request=request)
    payload = {s.refresh_api_phone_field or "phone_number": phone, "client": "sms"}
    headers = {"Content-Type": "application/json"}
    if s.refresh_api_token:
        headers["Authorization"] = f"Bearer {s.refresh_api_token}"

    try:
        resp = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=s.refresh_api_timeout or 30,
        )
    except requests.RequestException as exc:
        log.warning("SMS refresh API call failed for %s: %s", phone, exc)
        return SmsRefreshOutcome(
            status_code="error",
            title="خطأ",
            body="تعذّر تنفيذ التحديث حالياً. يرجى المحاولة لاحقاً.",
            error=str(exc),
        )

    try:
        data = resp.json()
    except ValueError:
        data = {}

    status_code = _dotted_get(data, s.refresh_api_status_path) or (
        "error" if resp.status_code >= 400 else ""
    )
    title = _dotted_get(data, s.refresh_api_title_path) or ""
    body = _dotted_get(data, s.refresh_api_body_path) or ""

    return SmsRefreshOutcome(
        status_code=str(status_code or "error"),
        title=str(title or ""),
        body=str(body or ""),
        http_status=resp.status_code,
        error=None if resp.ok else f"http_{resp.status_code}",
    )
