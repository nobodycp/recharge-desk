"""Provider credentials: DB-stored (encrypted) with a Sky env fallback."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from django.utils import timezone

from providers.contracts import PermanentError
from providers.crypto import decrypt_or_empty
from providers.sanitize import scrub

logger = logging.getLogger(__name__)

MAX_ERROR_LEN = 500


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str = field(repr=False)
    totp_secret: str = field(default="", repr=False)
    extra: dict[str, Any] = field(default_factory=dict, repr=False)


def normalize_totp_secret(raw: str) -> str:
    """Return a bare base32 secret from a raw secret or an ``otpauth://`` URI.

    Raises ``ValueError`` if the value is not a valid base32 TOTP secret.
    """
    import pyotp

    raw = (raw or "").strip()
    if not raw:
        return ""
    if raw.lower().startswith("otpauth://"):
        try:
            secret = pyotp.parse_uri(raw).secret
        except Exception as exc:  # pyotp raises ValueError/KeyError variants
            raise ValueError("invalid otpauth URI") from exc
    else:
        secret = raw
    secret = secret.replace(" ", "").upper()
    try:
        pyotp.TOTP(secret).now()
    except Exception as exc:  # binascii.Error / TypeError on bad base32
        raise ValueError("invalid TOTP secret") from exc
    return secret


def credentials_from_env() -> Credentials | None:
    """Legacy Sky credentials from ``SKY_SALES_*`` env vars, if complete."""
    user = os.environ.get("SKY_SALES_USER", "").strip()
    password = os.environ.get("SKY_SALES_PASSWORD", "")
    if not user or not password:
        return None
    totp = ""
    uri = os.environ.get("SKY_SALES_OTP_AUTH_URI", "").strip()
    raw_secret = os.environ.get("SKY_SALES_TOTP_SECRET", "").strip()
    try:
        totp = normalize_totp_secret(uri or raw_secret)
    except ValueError:
        totp = ""
    proxy = os.environ.get("SKY_SALES_PROXY", "").strip()
    extra = {"proxy_url": proxy} if proxy else {}
    return Credentials(user, password, totp, extra)


def load_extra(account) -> dict[str, Any]:
    try:
        data = json.loads(decrypt_or_empty(account.extra_encrypted) or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def has_unreadable_secrets(account) -> bool:
    """True if any stored secret field is set but cannot be decrypted."""
    return any(
        blob and not decrypt_or_empty(blob)
        for blob in (
            account.secret_encrypted,
            account.totp_secret_encrypted,
            account.extra_encrypted,
        )
    )


def get_credentials(provider_key: str) -> Credentials:
    """DB account first, else the Sky env fallback, else ``PermanentError``.

    A DB account whose secrets cannot be decrypted (rotated/mistyped key) is a
    config error; it deliberately does not fall back to env credentials.
    """
    from providers.models import ProviderAccount

    account = ProviderAccount.objects.filter(provider_key=provider_key).first()
    if account is not None and account.username and account.secret_encrypted:
        if has_unreadable_secrets(account):
            logger.warning(
                "Provider account secrets are undecryptable (check PROVIDER_SECRETS_KEY): %s",
                provider_key,
            )
            raise PermanentError("config_error")
        return Credentials(
            username=account.username,
            password=decrypt_or_empty(account.secret_encrypted),
            totp_secret=decrypt_or_empty(account.totp_secret_encrypted),
            extra=load_extra(account),
        )
    if provider_key == "sky":
        creds = credentials_from_env()
        if creds is not None:
            return creds
    raise PermanentError("config_error")


def _scrub(text: str, secrets: list[str]) -> str:
    text = scrub(text)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text[:MAX_ERROR_LEN]


def record_login(provider_key: str, ok: bool, error: str = "") -> None:
    """Persist the outcome of the latest login attempt (no-op without a DB account)."""
    from providers.models import ProviderAccount

    account = ProviderAccount.objects.filter(provider_key=provider_key).first()
    if account is None:
        return
    secrets = [
        decrypt_or_empty(account.secret_encrypted),
        decrypt_or_empty(account.totp_secret_encrypted),
        str(load_extra(account).get("proxy_url", "")),
    ]
    account.last_login_at = timezone.now()
    account.last_login_ok = ok
    account.last_error = "" if ok else _scrub(error or "", secrets)
    account.save(
        update_fields=["last_login_at", "last_login_ok", "last_error", "updated_at"]
    )
