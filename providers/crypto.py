"""Symmetric encryption for provider secrets (Fernet)."""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet
from django.conf import settings


def _fernet() -> Fernet:
    key = getattr(settings, "PROVIDER_SECRETS_KEY", "") or ""
    if not key:
        # Dev only: production settings require PROVIDER_SECRETS_KEY.
        digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
        return Fernet(base64.urlsafe_b64encode(digest))
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
