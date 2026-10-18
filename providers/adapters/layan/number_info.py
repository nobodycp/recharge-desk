"""Turn a Layan subscription page plus its usage block into ``NumberInfo``."""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from django.utils import timezone

from providers.contracts import NumberInfo

_MB_PER_GB = Decimal(1024)
_GB_QUANTUM = Decimal("0.01")
_UNLIMITED = frozenset({"unlimited", "غير محدود", "לא מוגבל", "ללא הגבלה"})
_DATE_FORMATS = ("%d/%m/%Y %H:%M", "%d/%m/%Y")


def parse_number_info(subscription: dict, usage: dict | None) -> NumberInfo:
    package = subscription.get("package") if isinstance(subscription.get("package"), dict) else {}
    usage = usage if isinstance(usage, dict) else {}
    number = usage.get("number") if isinstance(usage.get("number"), dict) else {}
    internet = _text(package.get("internet"))
    unlimited = internet.lower() in _UNLIMITED
    used_mb = _decimal(usage.get("internet_UsedMB"))
    size_mb = _decimal(usage.get("internet_Size"))
    if unlimited:
        data_used = _gb(used_mb if used_mb is not None else Decimal(0))
        data_total = None
    elif size_mb is not None and size_mb > 0:
        data_used = _gb(used_mb if used_mb is not None else Decimal(0))
        data_total = _gb(size_mb)
    else:
        data_used = _gb(used_mb) if used_mb else None
        data_total = Decimal(0) if size_mb == 0 and not unlimited else None

    sim = _text(subscription.get("simNumber")) or _text(usage.get("simNumber"))
    return NumberInfo(
        network=_text(package.get("companyName")),
        is_esim=False,
        status=_text(number.get("status")),
        plan_name=_text(package.get("name")),
        plan_price=_decimal(package.get("cost")),
        ends_at=_ends_at(subscription.get("endDate")),
        data_used_gb=data_used,
        data_total_gb=data_total,
        minutes_used=_count(usage.get("voice_Used")),
        minutes_total=_allowance(usage.get("voice_Size")),
        sms_used=_count(usage.get("messages_Used")),
        sms_total=_allowance(usage.get("messages_Size")),
        utilization_pct=_decimal(usage.get("package_Usage")),
        iccid=sim,
        data_unlimited=unlimited,
    )


def _text(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()


def _decimal(value: Any) -> Decimal | None:
    text = _text(value).replace(",", "")
    if not text:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    return number


def _gb(megabytes: Decimal | None) -> Decimal | None:
    if megabytes is None:
        return None
    return (megabytes / _MB_PER_GB).quantize(_GB_QUANTUM, rounding=ROUND_HALF_UP)


def _count(value: Any) -> int | None:
    number = _decimal(value)
    if number is None:
        return None
    return int(number.to_integral_value(rounding=ROUND_HALF_UP))


def _allowance(value: Any) -> int | None:
    """``0`` means the bundle does not include this, which the card already shows."""
    return _count(value)


def _ends_at(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            naive = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return timezone.make_aware(naive, timezone.get_default_timezone())
    return None
