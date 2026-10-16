"""Pure Sky top-up helpers: period days, price match, task payload, task status.

Days are ``amount * period_factor`` (a month is 30, a week is 7). The Arabic
label is not used: one Sky plan calls 360 days «12 عام».
"""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

_MONEY = Decimal("0.01")
_DONE_STATUS = "2"
_FAILED_MARKS = ("إلغ", "ملغ", "فشل", "رفض")


def _lower(row: dict) -> dict[str, Any]:
    return {str(key).lower(): value for key, value in row.items()}


def _int(row: dict, key: str) -> int | None:
    value = _lower(row).get(key)
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    if not number.is_finite() or number != number.to_integral_value():
        return None
    return int(number)


def period_days(row: dict) -> int | None:
    """Days this Sky period charges, or ``None`` when the row is unusable."""
    amount = _int(row, "amount")
    factor = _int(row, "period_factor")
    if amount is None or factor is None or amount <= 0 or factor <= 0:
        return None
    return amount * factor


def find_period(rows: list[dict], days: int) -> dict | None:
    """The single period whose length is exactly ``days``."""
    if days <= 0:
        return None
    matches = [row for row in rows if period_days(row) == days]
    return matches[0] if len(matches) == 1 else None


def quoted_cost(row: dict) -> Decimal | None:
    """Absolute shekel cost Sky quoted (its figure is negative: a balance debit)."""
    value = _lower(row).get("order_total_cost")
    if value is None:
        return None
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    return abs(number).quantize(_MONEY)


def costs_match(quoted: Decimal | None, expected: Decimal) -> bool:
    if quoted is None:
        return False
    return quoted == Decimal(expected).quantize(_MONEY)


def task_settled(row: dict | None) -> str:
    """``success``, ``failed`` or ``open`` from one ``GetTaskDetails`` row."""
    if not row:
        return "open"
    low = _lower(row)
    if str(low.get("status_id") or "").strip() == _DONE_STATUS:
        return "success"
    name = str(low.get("status_name") or "")
    if any(mark in name for mark in _FAILED_MARKS):
        return "failed"
    return "open"


def build_task(
    sim: dict,
    period: dict,
    costs: dict[str, str],
    *,
    phone: str,
    global_customer_id: str,
) -> dict:
    """The ``sim-top-up`` task body the Sky portal posts."""
    low = _lower(sim)
    period_low = _lower(period)
    period_id = str(period_low.get("id") or "")
    name = str(low.get("subscriber_name") or "").replace("\\", "").replace("'", "")
    description = (
        '<b id="task-title">تعديل زبون</b><br/>'
        f'شركة الاتصالات : {low.get("mno_name") or ""}<br/>'
        f"رقم الهاتف : {phone}<br/>"
        f'رقم الشريحة : {low.get("iccid") or ""}<br/>'
        f'الرزمة : {low.get("rate_plan_name") or ""}<br/>'
        f'مدة الخدمة : {period_low.get("plan_period") or ""}<br/>'
        f'سعر الطلبية : {costs["order_cost"]}<br/>'
        f'اعادة المال : {costs["order_refund"]}<br/>'
        f'سعر نهائي : {costs["order_total_cost"]}<br/>'
    )
    existing = {
        "subscriber_id": str(low.get("id") or ""),
        "msisdn": phone,
        "iccid": str(low.get("iccid") or ""),
        "rate_plan_id": str(low.get("rate_plan_id") or ""),
        "mno_id": str(low.get("mno_id") or ""),
        "status_id": str(low.get("status_id") or ""),
        "period_id": "",
        "subscriberName": name,
        "customer_id": str(low.get("customer_id") or ""),
        "subCustomer_id": str(low.get("sub_customer_id") or ""),
        "price": None,
        "vas_id": None,
        "vas_status": None,
        "vas_details": None,
        "auto_renew": str(low.get("auto_renew") or "0"),
        "secondary_iccid": str(low.get("secondary_iccid") or "0"),
        "secondary_msisdn": str(low.get("secondary_msisdn") or ""),
    }
    return {
        "actionName": "sim-top-up",
        "actionType": "تعديل زبون",
        "taskDescription": description,
        "confirmMessage": "",
        "existingData": existing,
        "newData": {
            **{key: existing[key] for key in (
                "subscriber_id", "msisdn", "iccid", "rate_plan_id", "mno_id",
                "secondary_iccid", "secondary_msisdn",
            )},
            "status_id": "",
            "period_id": period_id,
            "subscriber_name": name,
            "customer_id": global_customer_id or str(low.get("sub_customer_id") or ""),
            "sub_customer_id": str(low.get("sub_customer_id") or ""),
            "price": str(low.get("subscriber_price") or "0"),
            "vas_id": None,
            "vas_status": None,
            "vas_details": None,
            "sub_task_type_id": None,
            "sub_customer_obligo_update_value": costs["order_total_cost"],
            "temporary_msisdn": None,
            "orderRefund": costs["order_refund"],
            "orderCost": costs["order_cost"],
            "orderTotalCost": costs["order_total_cost"],
            "auto_renew": "0",
        },
    }


def task_args(task: dict, *, user_id: int, sim_id: str) -> list:
    return [
        "sim-top-up",
        user_id,
        sim_id,
        task["taskDescription"],
        json.dumps(task, ensure_ascii=False),
    ]
