"""Shared Palestinian mobile number rules for refresh flows."""
from __future__ import annotations

import re

ALLOWED_PHONE_PREFIXES: tuple[str, ...] = (
    "050",
    "051",
    "052",
    "053",
    "054",
    "055",
    "058",
)

PHONE_RE = re.compile(r"^05[0123458]\d{7}$")
PHONE_HTML_PATTERN = r"05[0123458][0-9]{7}"

PHONE_VALIDATION_ERROR_AR = (
    "الرقم يجب أن يتكوّن من 10 أرقام ويبدأ بـ "
    "050 أو 051 أو 052 أو 053 أو 054 أو 055 أو 058."
)


def is_valid_phone(value: str) -> bool:
    return bool(PHONE_RE.match((value or "").strip()))
