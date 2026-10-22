"""Pure helpers turning a Sky ``GetSubscribers4TasksByPatternV3`` row into ``NumberInfo``.

Every field is parsed on its own: a missing or unparseable value becomes
``None`` (``""`` for text) and never fails the whole check.
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Optional

from django.utils import timezone

from providers.contracts import NumberInfo

_DATETIME_FORMAT = "%d/%m/%Y %H:%M"
_DATE_FORMAT = "%d/%m/%Y"
_MB_PER_GB = Decimal(1024)
_GB_QUANTUM = Decimal("0.01")
# Anything beyond this is not a plausible usage/price figure; also keeps
# ``int()`` / ``quantize`` away from absurd exponents like "1e999999".
_MAX_ABS = Decimal(10) ** 12


def _get(row: dict, key: str) -> Any:
    """Value for ``key`` tolerating the upper/lower-case variants Sky sends."""
    for candidate in (key, key.upper(), key.lower()):
        if candidate in row:
            return row[candidate]
    return None


def _text(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "null" else text


def _decimal(value: Any) -> Optional[Decimal]:
    text = _text(value)
    if not text:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if not number.is_finite() or abs(number) >= _MAX_ABS:
        return None
    return number


def _int(value: Any) -> Optional[int]:
    number = _decimal(value)
    if number is None or number != number.to_integral_value():
        return None
    return int(number)


def _ends_at(row: dict) -> Optional[datetime]:
    for key, fmt in (
        ("rate_plan_end_date_time", _DATETIME_FORMAT),
        ("rate_plan_end_date", _DATE_FORMAT),
    ):
        text = _text(_get(row, key))
        if not text:
            continue
        try:
            naive = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return timezone.make_aware(naive, timezone.get_default_timezone())
    return None


_PELEPHONE = ("pelephone", "بليفون", "بلفون", "פלאפון")
_TALK_GB = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*GB\b", re.IGNORECASE)
_TALK_SMS = re.compile(r"SMS:\s*([0-9]+)", re.IGNORECASE)
_TALK_MINUTES = re.compile(r"דקות שיחה:\s*([0-9]+)")


def is_pelephone(network: str) -> bool:
    text = (network or "").strip().lower()
    return any(token in text for token in _PELEPHONE)


def parse_talkphone_balance(text: str) -> tuple[Optional[Decimal], Optional[int], Optional[int]]:
    """Used data, minutes, and SMS from Sky ``GetTalkphoneBalance``.

    The provider returns one Hebrew sentence, for example
    ``גלישה באינטרנט בארץ:20.7364 GB, הודעות SMS: 0, דקות שיחה: 0``.
    """
    raw = _text(text)
    data = None
    match = _TALK_GB.search(raw)
    if match:
        number = _decimal(match.group(1))
        if number is not None:
            data = number.quantize(_GB_QUANTUM, rounding=ROUND_HALF_UP)
    minutes = _int(_TALK_MINUTES.search(raw).group(1)) if _TALK_MINUTES.search(raw) else None
    sms = _int(_TALK_SMS.search(raw).group(1)) if _TALK_SMS.search(raw) else None
    return data, minutes, sms


def _data_total_gb(row: dict) -> Optional[Decimal]:
    megabytes = _decimal(_get(row, "allocated_mb"))
    if megabytes is None:
        return None
    return (megabytes / _MB_PER_GB).quantize(_GB_QUANTUM, rounding=ROUND_HALF_UP)


def matches_phone(row: dict, phone: str) -> bool:
    """True if ``row`` is exactly ``phone`` (``search_subscriber`` may return another row)."""
    phone = (phone or "").strip()
    significant = phone.lstrip("0")
    if not significant:
        return False
    if _text(_get(row, "ne_id")) == phone:
        return True
    return _text(_get(row, "msisdn")).endswith(significant)


def parse_number_info(row: dict) -> NumberInfo:
    return NumberInfo(
        network=_text(_get(row, "mno_name")),
        is_esim=_text(_get(row, "esim")).lower() == "esim",
        status=_text(_get(row, "status_name")),
        plan_name=_text(_get(row, "rate_plan_name")),
        plan_price=_decimal(_get(row, "rate_plan_price")),
        ends_at=_ends_at(row),
        data_used_gb=_decimal(_get(row, "total_data_in_billing_cycle")),
        data_total_gb=_data_total_gb(row),
        minutes_used=_int(_get(row, "total_voice_in_billing_cycle")),
        minutes_total=_int(_get(row, "allocated_minutes")),
        sms_used=_int(_get(row, "total_sms_in_billing_cycle")),
        sms_total=_int(_get(row, "allocated_sms")),
        utilization_pct=_decimal(_get(row, "utilization")),
        iccid=_text(_get(row, "iccid")),
    )
