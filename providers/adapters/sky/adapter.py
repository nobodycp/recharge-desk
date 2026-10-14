from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date
from typing import Any, Iterator

import requests

from providers.accounts import get_credentials
from providers.adapters.sky.client import (
    PROVIDER_KEY,
    SkySalesClient,
    SkySalesError,
    SkySalesSessionError,
)
from providers.contracts import (
    AuthRequired,
    BaseAdapter,
    Capability,
    PermanentError,
    RawResponse,
    TransientError,
)
from providers.sanitize import scrub

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


@contextmanager
def _sky_errors() -> Iterator[None]:
    """Translate Sky client failures into provider-gateway errors."""
    try:
        yield
    except SkySalesSessionError as exc:
        raise AuthRequired("session_expired") from exc
    except requests.RequestException as exc:
        raise TransientError("connection_error") from exc
    except SkySalesError as exc:
        raise PermanentError(scrub(str(exc))) from exc


class SkyAdapter(BaseAdapter):
    """Sky Sales Portal refresh via HTTP (sales-ps.sky5g.ps)."""

    key = PROVIDER_KEY
    capabilities = frozenset({Capability.REFRESH})
    timeout: int = 120
    needs_login = True

    def _stored_client(self) -> SkySalesClient:
        """Client for the session in Redis; ``AuthRequired`` if it can't be used."""
        client = SkySalesClient.from_store(get_credentials(PROVIDER_KEY))
        if client is None:
            raise AuthRequired("session not ready")
        try:
            usable = client.is_session_usable()
        except SkySalesError as exc:
            # Egress-IP detection through the account proxy failed.
            raise TransientError("proxy_error") from exc
        if not usable:
            raise AuthRequired("session not ready")
        return client

    def refresh(self, phone: str) -> RawResponse:
        phone = phone.strip()
        client = self._stored_client()
        with _sky_errors():
            result = client.refresh_sim(phone)
        reason = str(result.get("reason") or "")

        if reason in _SKY_SYSTEM_ERROR_REASONS:
            if result.get("error"):
                result["error"] = scrub(str(result["error"]))
            return RawResponse(
                text=json.dumps(result, ensure_ascii=False),
                json=result,
                status_code=0,
                error=result.get("error") or result.get("message"),
            )

        status_code = 200 if result.get("success") else 422
        return RawResponse(
            text=json.dumps(result, ensure_ascii=False),
            json=result,
            status_code=status_code,
        )

    def fetch_balance_rows(self, date_from: date, date_to: date) -> list[dict[str, Any]]:
        """Sky template-22 balance rows for the logged-in account."""
        client = self._stored_client()
        with _sky_errors():
            return client.fetch_balance_report(date_from, date_to)

    def login(self) -> None:
        creds = get_credentials(PROVIDER_KEY)
        client = SkySalesClient(credentials=creds)
        client.force_new_session(creds.username, creds.password)

    def ping(self) -> None:
        client = self._stored_client()
        with _sky_errors():
            client._probe_session()
