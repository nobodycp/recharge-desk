"""Pure helpers for Layan activate / extend charges."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

_MONEY = Decimal("0.01")
_SUCCESS_PHRASE = "سيتم تشغيل الرقم في الخلفيه"
_ERROR_MARKS = ("خطأ", "error", "failed", "unauthorized")


def is_active_status(status: str) -> bool:
    text = (status or "").strip()
    if not text:
        return False
    if text.lower() == "active":
        return True
    return text == "فعال"


def pick_three_month_sale(sales: list[dict]) -> dict | None:
    for sale in sales:
        if not isinstance(sale, dict):
            continue
        duration = sale.get("duration")
        if duration == 90 or duration == "90":
            return sale
        text = str(duration or "")
        if "ثلاثة" in text:
            return sale
    return None


def parse_cost_quote(body: Any) -> Decimal | None:
    if body is None or isinstance(body, bool):
        return None
    if isinstance(body, dict):
        for key in ("cost", "Cost", "value", "amount"):
            if key in body:
                return parse_cost_quote(body[key])
        return None
    try:
        number = Decimal(str(body).strip().replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None
    if not number.is_finite():
        return None
    return number.quantize(_MONEY, rounding=ROUND_HALF_UP)


def money_match(quoted: Decimal | None, expected: Decimal) -> bool:
    if quoted is None:
        return False
    return quoted == Decimal(expected).quantize(_MONEY, rounding=ROUND_HALF_UP)


def is_charge_success(status_code: int, body: str | dict | None) -> bool:
    if status_code != 200:
        return False
    if body is None:
        return True
    if isinstance(body, dict):
        text = str(body.get("message") or body.get("Message") or body)
    else:
        text = str(body)
    stripped = text.strip()
    if not stripped:
        return True
    if _SUCCESS_PHRASE in stripped:
        return True
    low = stripped.lower()
    if any(mark in stripped or mark in low for mark in _ERROR_MARKS):
        return False
    return "<html" not in low and "exception" not in low
