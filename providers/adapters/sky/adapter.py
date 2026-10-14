from __future__ import annotations

import json

from providers.adapters.sky.client import execute_refresh
from providers.contracts import BaseAdapter, Capability, RawResponse

_SKY_SYSTEM_ERROR_REASONS = frozenset(
    {
        "proxy_error",
        "login_error",
        "session_error",
        "otp_required",
        "config_error",
        "api_error",
        "connection_error",
    }
)


class SkyAdapter(BaseAdapter):
    """Sky Sales Portal refresh via HTTP (sales-ps.sky5g.ps)."""

    key = "sky"
    capabilities = frozenset({Capability.REFRESH})
    timeout: int = 120

    def refresh(self, phone: str) -> RawResponse:
        result = execute_refresh(phone)
        text = json.dumps(result, ensure_ascii=False)
        reason = str(result.get("reason") or "")

        if reason in _SKY_SYSTEM_ERROR_REASONS:
            return RawResponse(
                text=text,
                json=result,
                status_code=0,
                error=result.get("error") or result.get("message"),
            )

        status_code = 200 if result.get("success") else 422
        return RawResponse(text=text, json=result, status_code=status_code)
