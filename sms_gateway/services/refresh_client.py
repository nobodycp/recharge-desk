"""Refresh source client for the SMS gateway.

When no external gateway URL is configured the refresh engine is called
in-process (no HTTP). When an admin sets ``refresh_api_url`` the service
POSTs to that URL instead, so the source can be swapped to an external API
from the GUI without a code change. HTTP responses are parsed using the
configurable dotted paths.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

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


def _call_in_process(phone: str) -> SmsRefreshOutcome:
    """Run the refresh engine in-process (no HTTP).

    Used when no external gateway URL is configured. Calling our own public
    API over HTTP would loop back out through the public hostname and get
    blocked by Cloudflare's bot challenge (HTTP 403 "Just a moment"), so for
    the internal engine we invoke the service directly.
    """
    from phone_refresh.models import RefreshSource
    from phone_refresh.services.refresh_service import refresh_phone

    try:
        result = refresh_phone(phone, source=RefreshSource.SMS)
    except Exception as exc:  # noqa: BLE001 — never break inbound processing
        log.warning("in-process refresh failed for %s: %s", phone, exc)
        return SmsRefreshOutcome(
            status_code="error",
            title="خطأ",
            body="تعذّر تنفيذ التحديث حالياً. يرجى المحاولة لاحقاً.",
            error=str(exc),
        )
    return SmsRefreshOutcome(
        status_code=result.status.code,
        title=result.message_title or "",
        body=result.message_body or "",
        http_status=200,
        error=None,
    )


def call_refresh_api(
    phone: str,
    *,
    settings_obj: SmsGatewaySettings | None = None,
    request=None,
) -> SmsRefreshOutcome:
    """Call the configured refresh source for ``phone`` and parse the reply.

    When ``refresh_api_url`` is empty the internal engine is called directly
    in-process; otherwise the configured external HTTP gateway is used.
    """
    s = settings_obj or SmsGatewaySettings.load()
    url = (s.refresh_api_url or "").strip()
    if not url:
        return _call_in_process(phone)

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
