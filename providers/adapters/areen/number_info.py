"""Turn an Areen search plus its subscription-details window into ``NumberInfo``.

The home screen sends the remaining days, the monthly fee, and the last charge.
The details window sends the plan (المسار), the status, the SIM serial, and
either a usage row ``0.145 من 400`` or the WE labels (package size and remaining
gigabytes). The end date is the check day plus the remaining days. When those
days are zero, the end date is 30 days after الشحنة الأخيرة. Minutes and SMS
are excluded unless that window says they are unlimited.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from django.utils import timezone

from providers.contracts import NumberInfo

_COMPONENT = re.compile(r'_cdo_\("([^"]+)",([A-Za-z0-9]+)')
_SET_TEXT = re.compile(r'([A-Za-z0-9]+)\.setText\("((?:\\.|[^"\\])*)"\)')
_TEXT = re.compile(r'text:"((?:\\.|[^"\\])*)"')
_BUTTON = re.compile(r'([A-Za-z0-9]+)=new Ext\.button\.Button\(\{')
_ROOT = re.compile(r'([A-Za-z0-9]+)\.rootObj=true')
_STORE = re.compile(r"Obj=([A-Za-z0-9]+)&Evt=data")
_QUANTITY = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*من\s*([0-9]+(?:\.[0-9]+)?)")
_CHARGE_ON = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_TOAST = re.compile(r'toastr\.error\("([^"]*)"\)')
_SPACE = re.compile(r"\s+")
_PACKAGE_DAYS = 30

NOT_FOUND = "غير موجود"
DETAILS_BUTTON = "عرض تفاصيل الاشتراك"

# Home-screen value labels, and the same facts when the details window draws them.
_HOME = {
    "lblStatus": "status",
    "lblSupplier": "network",
    "lblMsar": "plan_name",
    "lblSimNum": "iccid",
    "lblRemainingDays": "days",
    "lblCostMonthly": "plan_price",
    "lblLastCharge": "last_charge",
}
_DETAILS = {
    "edtMobStatusDesc": "status",
    "edtAccProgram": "plan_name",
    "edtSIM": "iccid",
}


def _unescape(value: str) -> str:
    return value.replace(r"\"", '"').replace(r"\n", "\n")


def component_map(script: str) -> dict[str, str]:
    """uniGUI ``_cdo_("edtCelNum", O438)`` as ``{name: object id}``."""
    return {name: obj for name, obj in _COMPONENT.findall(script or "")}


def named_texts(script: str) -> dict[str, str]:
    """Label text keyed by the stable ``_cdo_`` name.

    A later ``setText`` replaces the text the constructor was created with.
    """
    script = script or ""
    by_id: dict[str, str] = {}
    names: dict[str, str] = {}
    for match in _COMPONENT.finditer(script):
        name, obj = match.group(1), match.group(2)
        names[name] = obj
        chunk = script[max(0, match.start() - 800) : match.start()]
        found = _TEXT.findall(chunk)
        if found:
            by_id[obj] = _unescape(found[-1])
    for obj, text in _SET_TEXT.findall(script):
        by_id[obj] = _unescape(text)
    return {name: by_id[obj] for name, obj in names.items() if obj in by_id}


def apply_set_text(names: dict[str, str], script: str) -> dict[str, str]:
    """``setText`` calls in a search response, keyed by the home-screen name."""
    by_id = {obj: _unescape(text) for obj, text in _SET_TEXT.findall(script or "")}
    return {name: by_id[obj] for name, obj in names.items() if obj in by_id}


def button_id(script: str, label: str) -> str:
    for match in _BUTTON.finditer(script or ""):
        chunk = script[match.end() : match.end() + 250]
        found = _TEXT.search(chunk)
        if found and _unescape(found.group(1)) == label:
            return match.group(1)
    return ""


def root_window(script: str) -> str:
    found = _ROOT.search(script or "")
    return found.group(1) if found else ""


def store_ids(script: str) -> list[str]:
    return list(dict.fromkeys(_STORE.findall(script or "")))


def main_window(script: str, anchor: str) -> str:
    """The window that owns ``anchor`` (the phone field's stable name)."""
    roots = [(match.start(), match.group(1)) for match in _ROOT.finditer(script or "")]
    if not roots:
        return ""
    at = (script or "").find(f'_cdo_("{anchor}"')
    if at != -1:
        prior = [obj for pos, obj in roots if pos < at]
        if prior:
            return prior[-1]
    return roots[0][1]


def missing_subscription(script: str) -> bool:
    return NOT_FOUND in (script or "")


def error_toast(script: str) -> str:
    found = _TOAST.search(script or "")
    return _unescape(found.group(1)) if found else ""


def parse_quantity(text: str) -> tuple[Decimal | None, Decimal | None]:
    """``0.145 من 400`` is used gigabytes out of the bundle size."""
    match = _QUANTITY.search(text or "")
    if match is None:
        return None, None
    try:
        used, total = Decimal(match.group(1)), Decimal(match.group(2))
    except InvalidOperation:
        return None, None
    if not used.is_finite() or not total.is_finite():
        return None, None
    return used, total


def _decimal(value: Any) -> Decimal | None:
    text = _SPACE.sub("", str(value or "")).replace(",", "")
    text = "".join(ch for ch in text if ch.isdigit() or ch in ".-")
    if not text or text in {".", "-", "-."}:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _days(value: Any) -> int | None:
    number = _decimal(value)
    if number is None or number < 0:
        return None
    return int(number.to_integral_value())


def _plan(value: str) -> str:
    return _SPACE.sub(" ", (value or "").replace("\u00a0", " ")).strip()


def _charge_date(value: str) -> date | None:
    """``19/04/2026 - 44.00 ش`` keeps the day, month, and year."""
    match = _CHARGE_ON.search(value or "")
    if match is None:
        return None
    day, month, year = (int(match.group(index)) for index in (1, 2, 3))
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _last_charge(script: str) -> str:
    """The home label text, even when that label is missing from the name map."""
    for _, text in _SET_TEXT.findall(script or ""):
        shown = _unescape(text)
        if _charge_date(shown) is not None and "ش" in shown:
            return shown.strip()
    return ""


def _grid_status(rows: list[dict] | None) -> str:
    """Subscription grid: phone, name, then الوضعية."""
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        phone = str(row.get("0") or "").strip()
        status = str(row.get("2") or "").strip()
        if status and phone.isdigit():
            return status
    return ""


def _voice_counts(values: dict[str, str], fields: dict[str, str]) -> None:
    """WE window: calls and SMS are open-ended and still show a used count."""
    if not any("بدون تحديد" in text for text in values.values()):
        return
    fields["voice_unlimited"] = "1"
    minutes = _decimal(values.get("lblCallMinute"))
    messages = _decimal(values.get("lblSMS"))
    if minutes is not None:
        fields["minutes_used"] = str(int(minutes))
    if messages is not None:
        fields["sms_used"] = str(int(messages))


def _we_usage(values: dict[str, str], fields: dict[str, str]) -> None:
    """``lblNetDataPKG`` is the size and ``lblDataNetRem`` is what is left."""
    if fields.get("used"):
        return
    total = _decimal(values.get("lblNetDataPKG"))
    remaining = _decimal(values.get("lblDataNetRem"))
    if total is None or remaining is None:
        return
    used = total - remaining
    if used < 0:
        used = Decimal(0)
    fields["used"] = str(used)
    fields["total"] = str(total)


def fields_from_parts(
    names: dict[str, str],
    search_script: str,
    details_script: str,
    rows: list[dict],
    customer_rows: list[dict] | None = None,
) -> dict[str, str]:
    """One dict of card fields. Details win for the plan, status, and SIM."""
    values = apply_set_text(names, search_script)
    values.update(named_texts(details_script))
    fields = {key: (values.get(uname) or "").strip() for uname, key in _HOME.items()}
    if not fields.get("last_charge"):
        fields["last_charge"] = _last_charge(search_script)
    for uname, key in _DETAILS.items():
        text = (values.get(uname) or "").strip()
        if text:
            fields[key] = text
    if not fields.get("status"):
        fields["status"] = _grid_status(customer_rows)
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = str(row.get("0") or "")
        amount = str(row.get("1") or "")
        used, total = parse_quantity(amount)
        if used is not None and ("انترنت" in label or "إنترنت" in label or not fields.get("used")):
            if "انترنت" in label or "إنترنت" in label or "used" not in fields:
                fields["used"] = str(used)
                fields["total"] = str(total)
        if "ثمن الاشتراك" in label and not fields.get("plan_price"):
            fields["plan_price"] = amount
    _we_usage(values, fields)
    _voice_counts(values, fields)
    return fields


def _at_noon(day: date):
    moment = datetime.combine(day, time(12, 0))
    return timezone.make_aware(moment, timezone.get_default_timezone())


def build_number_info(fields: dict, today: date) -> NumberInfo:
    days = _days(fields.get("days"))
    ends_at = None
    charged = _charge_date(fields.get("last_charge") or "")
    if days == 0 and charged is not None:
        ends_at = _at_noon(charged + timedelta(days=_PACKAGE_DAYS))
    elif days is not None:
        ends_at = _at_noon(today + timedelta(days=days))
    used, total = parse_quantity(f"{fields.get('used') or ''} من {fields.get('total') or ''}")
    if used is None:
        used = _decimal(fields.get("used"))
        total = _decimal(fields.get("total"))
    if used is None and total is None:
        total = Decimal(0)
    voice_open = fields.get("voice_unlimited") == "1"
    minutes_used = _days(fields.get("minutes_used")) if voice_open else None
    sms_used = _days(fields.get("sms_used")) if voice_open else None
    return NumberInfo(
        network=(fields.get("network") or "").strip(),
        status=(fields.get("status") or "").strip(),
        plan_name=_plan(fields.get("plan_name") or ""),
        plan_price=_decimal(fields.get("plan_price")),
        ends_at=ends_at,
        data_used_gb=used,
        data_total_gb=total,
        minutes_used=minutes_used,
        minutes_total=None if voice_open else 0,
        sms_used=sms_used,
        sms_total=None if voice_open else 0,
        minutes_unlimited=voice_open,
        sms_unlimited=voice_open,
        iccid=(fields.get("iccid") or "").strip(),
    )
