"""HTTP helpers (client IP behind reverse proxies)."""
from __future__ import annotations

import ipaddress

from django.conf import settings
from django.http import HttpRequest


def _is_valid_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value.strip())
        return True
    except ValueError:
        return False


def _first_valid_ip(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.split(",")[0].strip()
    if candidate and _is_valid_ip(candidate):
        return candidate
    return None


def get_client_ip(request: HttpRequest | None) -> str | None:
    """Return the client IP, honoring trusted proxy headers when enabled.

    When ``settings.TRUST_FORWARDED_FOR`` is True (production behind
    Coolify/Cloudflare), prefer:

    1. ``CF-Connecting-IP`` (Cloudflare — most reliable)
    2. ``X-Forwarded-For`` (first hop in the chain)
    3. ``REMOTE_ADDR`` (direct connection / fallback)
    """
    if request is None:
        return None

    meta = request.META
    if getattr(settings, "TRUST_FORWARDED_FOR", False):
        cf_ip = _first_valid_ip(meta.get("HTTP_CF_CONNECTING_IP"))
        if cf_ip:
            return cf_ip

        xff_ip = _first_valid_ip(meta.get("HTTP_X_FORWARDED_FOR"))
        if xff_ip:
            return xff_ip

    remote = (meta.get("REMOTE_ADDR") or "").strip()
    if remote and _is_valid_ip(remote):
        return remote
    return remote or None
