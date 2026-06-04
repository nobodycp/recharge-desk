"""Subscriber number extraction for inbound SMS.

Accepts Palestinian mobile numbers 050-055 only (per requirement),
normalizing Arabic-Indic digits and stripping separators so a number
embedded in free text ("حدثلي 0555544071 شكرا") is still found.
"""
from __future__ import annotations

import re

# 050-055 only.
SMS_PHONE_RE = re.compile(r"^05[0-5]\d{7}$")
# Search pattern across free text (after digit normalization + separator strip).
_SEARCH_RE = re.compile(r"05[0-5]\d{7}")

# Arabic-Indic and Eastern-Arabic digit maps → ASCII.
_DIGIT_MAP = {ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")}
_DIGIT_MAP.update({ord(c): str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")})


def normalize_digits(text: str) -> str:
    return (text or "").translate(_DIGIT_MAP)


def extract_number(text: str) -> str | None:
    """Return the first 050-055 ten-digit number found in ``text``, else None."""
    if not text:
        return None
    normalized = normalize_digits(text)
    # Drop common separators so "059-555-4071" style inputs still match.
    compact = re.sub(r"[\s\-\u200f\u200e().]+", "", normalized)
    match = _SEARCH_RE.search(compact)
    return match.group(0) if match else None


def is_valid_number(value: str) -> bool:
    return bool(SMS_PHONE_RE.match((value or "").strip()))
