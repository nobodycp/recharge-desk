"""Palestinian mobile prefix rules for the sales entry screen."""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

ALLOWED_SALE_PHONE_PREFIXES: tuple[str, ...] = (
    "050",
    "051",
    "052",
    "053",
    "054",
    "055",
)

SALE_PHONE_RE = re.compile(r"^05[0-5]\d{7}$")

SALE_PHONE_PREFIX_ERROR = _("صحصح رقم الشريحه اكتب")


def normalize_sale_phone(raw: str) -> str:
    """Digits-only; ``972…`` → ``0…``; 9-digit ``5…`` → ``05…``."""
    d = re.sub(r"\D", "", str(raw or "").strip())
    if not d:
        return ""
    if d.startswith("972"):
        d = "0" + d[3:]
    if len(d) == 9 and d[0] == "5":
        d = "0" + d
    return d


def validate_sale_phone_prefix(value: str) -> str:
    """Return a normalized 050–055 mobile, or raise ``ValidationError``."""
    normalized = normalize_sale_phone(value)
    if not SALE_PHONE_RE.match(normalized):
        raise ValidationError(SALE_PHONE_PREFIX_ERROR)
    return normalized
