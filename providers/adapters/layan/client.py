"""Layan portal HTTP client: login + TOTP, then the accounting charges table."""

from __future__ import annotations

import json
import time
from datetime import date, timedelta
from typing import Any

import requests

from providers.accounts import Credentials
from providers.crypto import decrypt_or_empty, encrypt
from providers.sessions import SessionStore

try:
    import pyotp
except ImportError:  # pragma: no cover
    pyotp = None  # type: ignore

API = "https://api.layan-t.net/api/"
PROVIDER_KEY = "layan"
REQUEST_TIMEOUT = 40
# Portal JWTs last 8 hours; drop the session a minute early.
SESSION_SKEW_SECONDS = 60


class LayanError(Exception):
    """Layan refused a call or the account cannot sign in."""


class LayanSessionError(LayanError):
    """The stored JWT is missing or no longer accepted."""


def _session_blob(data: dict) -> dict[str, Any]:
    return {
        "jwt": str(data.get("jwt") or ""),
        "expires_at": float(data.get("expires_at") or 0),
        "logged_in_at": float(data.get("logged_in_at") or 0),
    }


def _save_durable(blob: dict) -> None:
    """Keep the JWT on the account row so the next process reuses it."""
    from providers.models import ProviderConnection

    account = ProviderConnection.for_key(PROVIDER_KEY)
    account.session_encrypted = encrypt(json.dumps(_session_blob(blob)))
    account.save(update_fields=["session_encrypted", "updated_at"])


def _load_durable() -> dict | None:
    from providers.models import ProviderConnection

    account = ProviderConnection.objects.filter(provider_key=PROVIDER_KEY).first()
    if account is None or not account.session_encrypted:
        return None
    raw = decrypt_or_empty(account.session_encrypted)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict) or not data.get("jwt"):
        return None
    return _session_blob(data)


def _totp_code(secret: str) -> str:
    if pyotp is None:
        raise LayanError("pyotp is not installed")
    if not secret:
        raise LayanError("TOTP secret is missing")
    return pyotp.TOTP(secret).now()


class LayanClient:
    def __init__(
        self,
        credentials: Credentials | None = None,
        *,
        jwt: str = "",
        expires_at: float = 0,
    ):
        self.credentials = credentials
        self.jwt = jwt
        self.expires_at = float(expires_at or 0)
        self.logged_in_at = 0.0
        self.http = requests.Session()

    def _headers(self, auth: bool) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "LANG": "ar",
            "Origin": "https://www.layan-t.net",
            "Referer": "https://www.layan-t.net/",
        }
        if auth:
            headers["Authorization"] = f"Bearer {self.jwt}"
        return headers

    def _post(self, path: str, payload: dict, *, auth: bool) -> requests.Response:
        return self.http.post(
            API + path,
            json=payload,
            headers=self._headers(auth),
            timeout=REQUEST_TIMEOUT,
        )

    def _get(self, path: str) -> requests.Response:
        return self.http.get(
            API + path,
            headers=self._headers(True),
            timeout=REQUEST_TIMEOUT,
        )

    def is_session_usable(self) -> bool:
        return bool(self.jwt) and self.expires_at > time.time() + SESSION_SKEW_SECONDS

    def save_session(self) -> None:
        blob = {
            "jwt": self.jwt,
            "expires_at": self.expires_at,
            "logged_in_at": self.logged_in_at,
        }
        SessionStore(PROVIDER_KEY).save(blob)
        _save_durable(blob)

    @classmethod
    def from_store(cls, credentials: Credentials | None = None) -> LayanClient | None:
        data = SessionStore(PROVIDER_KEY).load()
        if not data or not data.get("jwt"):
            data = _load_durable()
            if data and data.get("jwt"):
                # The in-memory store died with the process; put the same JWT back.
                SessionStore(PROVIDER_KEY).save(data)
        if not data or not data.get("jwt"):
            return None
        client = cls(
            credentials,
            jwt=str(data.get("jwt") or ""),
            expires_at=float(data.get("expires_at") or 0),
        )
        client.logged_in_at = float(data.get("logged_in_at") or 0)
        return client

    def force_new_session(self, username: str, password: str) -> None:
        secret = self.credentials.totp_secret if self.credentials else ""
        response = self._post(
            "Auth/Login",
            {"username": username, "password": password},
            auth=False,
        )
        if response.status_code != 200:
            raise LayanError(f"login failed ({response.status_code})")
        body = response.json()
        status = body.get("statusCode")
        data = body.get("data") or {}
        if status == 200 and data.get("jwt"):
            token = data["jwt"]
        elif status == 202 and data.get("verificationMethod") == 2:
            token = self._verify(data.get("requestId") or "", _totp_code(secret))
        else:
            raise LayanError("login needs a verification method this account cannot complete")
        self.jwt = token
        self.expires_at = _jwt_expiry(token)
        self.logged_in_at = time.time()
        self.save_session()

    def _verify(self, request_id: str, code: str) -> str:
        response = self._post(
            "Auth/VerifyCode",
            {"requestId": request_id, "code": code},
            auth=False,
        )
        if response.status_code != 200:
            raise LayanError("OTP rejected")
        token = (response.json() or {}).get("jwt") or ""
        if not token:
            raise LayanError("OTP rejected")
        return token

    def fetch_subscription(self, phone: str) -> list[dict[str, Any]]:
        """Rows from the subscription page (``GetSubscribtion``). Empty if unknown."""
        body = self._authed_json(
            "Subscribtions/GetSubscribtion", {"PhoneNumber": phone}
        )
        if isinstance(body, dict):
            body = body.get("data")
        return body if isinstance(body, list) else []

    def fetch_usage(self, phone: str) -> dict[str, Any] | None:
        """Package-usage block. ``None`` when Layan has no usage for this line."""
        try:
            body = self._authed_json(
                "Subscribtions/CheckSubscription", {"Number": phone}
            )
        except LayanError as exc:
            if isinstance(exc, LayanSessionError):
                raise
            return None
        return body if isinstance(body, dict) else None

    def _authed_json(self, path: str, payload: dict) -> Any:
        if not self.jwt:
            raise LayanSessionError("session expired")
        response = self._post(path, payload, auth=True)
        if response.status_code in (401, 403):
            raise LayanSessionError("session expired")
        if response.status_code != 200:
            raise LayanError(f"{path} failed ({response.status_code})")
        try:
            return response.json()
        except ValueError as exc:
            raise LayanError(f"{path} failed") from exc

    def get_sales_by_number(self, phone: str) -> list[dict[str, Any]]:
        """Promo / sale packages available for ``phone``."""
        if not self.jwt:
            raise LayanSessionError("session expired")
        response = self._get(f"Subscribtions/GetSalesByNumber/{phone}")
        if response.status_code in (401, 403):
            raise LayanSessionError("session expired")
        if response.status_code != 200:
            raise LayanError(f"GetSalesByNumber failed ({response.status_code})")
        try:
            body = response.json()
        except ValueError as exc:
            raise LayanError("GetSalesByNumber failed") from exc
        return body if isinstance(body, list) else []

    def get_cost_before_activate(
        self,
        phone: str,
        *,
        duration: int | None,
        sale_id: str | None,
    ) -> Any:
        """Quoted shekel cost for activate/extend before the charge is sent."""
        return self._authed_json(
            "Deals/GetCostBeforeActivate",
            {"Number": phone, "Duration": duration, "SaleId": sale_id},
        )

    def extend_line(
        self,
        phone: str,
        *,
        duration: int | None,
        sale_id: str | None,
        user_paid: bool = True,
    ) -> tuple[int, str]:
        return self._deal_post(
            "Deals/Extend",
            {
                "Number": phone,
                "Duration": duration,
                "SaleId": sale_id,
                "UserPaid": user_paid,
            },
        )

    def activate_line(
        self,
        phone: str,
        *,
        duration: int | None,
        sale_id: str | None,
        end_date: str | None,
        user_paid: bool = True,
    ) -> tuple[int, str]:
        payload = {
            "Number": phone,
            "Duration": duration,
            "SaleId": sale_id,
            "UserPaid": user_paid,
        }
        if end_date:
            payload["endDate"] = end_date
        return self._deal_post("Deals/ActivateLine", payload)

    def _deal_post(self, path: str, payload: dict) -> tuple[int, str]:
        if not self.jwt:
            raise LayanSessionError("session expired")
        response = self._post(path, payload, auth=True)
        if response.status_code in (401, 403):
            raise LayanSessionError("session expired")
        text = (response.text or "").strip()
        if not text:
            try:
                body = response.json()
            except ValueError:
                body = None
            if body is not None:
                text = body if isinstance(body, str) else str(body)
        return response.status_code, text

    def ping(self) -> None:
        response = self._get("Charges/GetChargeTypes")
        if response.status_code in (401, 403):
            raise LayanSessionError("session expired")
        if response.status_code != 200:
            raise LayanError(f"ping failed ({response.status_code})")

    def fetch_accounting(self, date_from: date, date_to: date) -> list[dict[str, Any]]:
        """Accounting rows (``Charged``) for the period, one page or paged."""
        # Ask through the next midnight so a local evening charge is not cut off;
        # the report parser drops anything outside the requested dates.
        payload_dates = {
            "From": f"{date_from.isoformat()}T00:00:00.000Z",
            "To": f"{(date_to + timedelta(days=1)).isoformat()}T00:00:00.000Z",
        }
        first = self._charges_page(payload_dates, start=0, length=-1)
        rows = list(first.get("data") or [])
        total = int(first.get("recordsFiltered") or len(rows))
        if len(rows) >= total:
            return rows
        start = len(rows)
        while start < total:
            page = self._charges_page(payload_dates, start=start, length=500)
            batch = list(page.get("data") or [])
            if not batch:
                break
            rows.extend(batch)
            start += len(batch)
        return rows

    def _charges_page(self, dates: dict[str, str], *, start: int, length: int) -> dict:
        if not self.jwt:
            raise LayanSessionError("session expired")
        response = self._post(
            "Charges/GetAllDt",
            {
                "draw": 1,
                "start": start,
                "length": length,
                "search": {"value": "", "regex": False},
                "customSearch": {
                    "UserFullName": "",
                    "CustomerFullName": "",
                    "IdentityNum": "",
                    "Asc_Order": None,
                    "Charged": True,
                    "UserType": None,
                    "ChargeType": None,
                    "PhoneNumber": "",
                    **dates,
                },
            },
            auth=True,
        )
        if response.status_code in (401, 403):
            raise LayanSessionError("session expired")
        if response.status_code != 200:
            raise LayanError(f"accounting report failed ({response.status_code})")
        body = response.json()
        if not isinstance(body, dict):
            raise LayanError("accounting report failed")
        return body


def _jwt_expiry(token: str) -> float:
    """``exp`` claim, or eight hours from now when the token is not a JWT."""
    import base64
    import json

    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        payload = json.loads(base64.urlsafe_b64decode(part))
        return float(payload["exp"])
    except Exception:
        return time.time() + 8 * 3600
