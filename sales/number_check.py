"""Sales «فحص الرقم»: submit a number check, poll it, build the result card.

Mirrors ``phone_refresh.services.refresh_jobs``: the web request never talks to
the provider. ``submit_number_check`` validates, applies the provider gate and
queues a ``number_info`` job (run by ``providers.tasks.run_number_info_job``);
``check_payload`` turns the job state into the JSON the page polls.

The only provider data that ever reaches the browser is the server-rendered
card HTML. The provider's own price (``NumberInfo.plan_price``) stays on the job
result and is never part of the card.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone
from redis.exceptions import LockError

from accounts.permissions import is_management
from phone_refresh.models import SystemSettings
from phone_refresh.validation import PHONE_RE
from providers import jobs, operations
from providers.contracts import Capability, NumberInfo
from providers.models import ProviderJob
from providers.redis_conn import get_redis
from providers.registry import ADAPTERS
from providers.service import service_state
from providers.sessions import NoLuaLock
from providers.tasks import run_number_info_job
from sales.models import Sale

log = logging.getLogger(__name__)

SERVICE = "number_info"
CARD_TEMPLATE = "sales/partials/number_check_card.html"

SUBMIT_LOCK_TIMEOUT_SECONDS = 10
SUBMIT_LOCK_WAIT_SECONDS = 3

DASH = "—"
MSG_INVALID_PHONE = "رقم غير صحيح"
MSG_NO_SALE = "لا توجد عملية بيع سابقة لهذا الرقم"
MSG_UNAVAILABLE = "فحص الرقم غير متاح لهذا المزوّد"
MSG_BUSY = "الخدمة مشغولة حالياً، حاول بعد قليل"
MSG_EXPIRED = "انتهت صلاحية الطلب، أعد المحاولة."
MSG_STILL_RUNNING = "الطلب ما زال قيد المعالجة، حاول بعد دقيقة"

# Gate states that are recorded as one operations row, mapped to the code the
# classifier knows (see ``providers.service._GATE_CODES``).
_GATE_CODES = {"off": "provider_disabled", "not_configured": "not_configured"}


@dataclass
class CheckTicket:
    """Either a ready ``payload`` (no job needed) or a ``job_id`` to poll."""

    job_id: str | None
    payload: dict | None


def _error(message: str, **extra) -> dict:
    return {"status": "error", "message": message, **extra}


def _source(user) -> str:
    return "management" if is_management(user) else "employee"


def last_sale_for_phone(phone: str) -> Sale | None:
    """Newest sale whose reference number is ``phone`` (the refresh routing rule)."""
    return (
        Sale.objects.filter(reference_number=phone)
        .select_related("company", "product__line")
        .order_by("-created_at", "-pk")
        .first()
    )


@contextmanager
def _submit_lock(phone: str):
    """Serialise find-or-create per phone; never blocks submits on Redis trouble."""
    lock = None
    acquired = False
    try:
        lock = get_redis().lock(
            f"number-info-submit:{phone}",
            timeout=SUBMIT_LOCK_TIMEOUT_SECONDS,
            blocking_timeout=SUBMIT_LOCK_WAIT_SECONDS,
            lock_class=NoLuaLock,
        )
        acquired = lock.acquire()
    except Exception:  # noqa: BLE001 — fall through and create the job anyway
        log.warning("number info submit lock unavailable for %s", phone, exc_info=True)
    try:
        yield
    finally:
        if acquired:
            try:
                lock.release()
            except LockError:
                log.warning("number info submit lock expired before release for %s", phone)


def _record_gate(provider_key: str, code: str, *, phone, user, sale) -> None:
    """One operations row for a check stopped at the gate (never raises)."""
    operations.record(
        provider_key,
        SERVICE,
        operations.classify_code(SERVICE, code),
        phone=phone,
        source=_source(user),
        user_id=user.pk,
        sale_id=sale.pk,
    )


def submit_number_check(phone: str, *, user) -> CheckTicket:
    """Start (or join) a number check for ``phone``.

    Answers without a job for an invalid number, a number without a sale, an
    unsupported provider, a gate failure (one operations row) and an enqueue
    failure (no row: nothing reached the provider).
    """
    phone = (phone or "").strip()
    if not PHONE_RE.match(phone):
        return CheckTicket(None, _error(MSG_INVALID_PHONE))

    sale = last_sale_for_phone(phone)
    if sale is None:
        return CheckTicket(None, _error(MSG_NO_SALE))

    company = sale.company
    provider_key = (company.provider_key or "").strip().lower()
    state = service_state(company, Capability.NUMBER_INFO)
    if state.code != "active":
        code = _GATE_CODES.get(state.code)
        if not provider_key or provider_key not in ADAPTERS or code is None:
            return CheckTicket(None, _error(MSG_UNAVAILABLE))
        _record_gate(provider_key, code, phone=phone, user=user, sale=sale)
        return CheckTicket(
            None, _error(operations.classify_code(SERVICE, code).message)
        )

    wait = SystemSettings.get().refresh_wait_seconds
    with _submit_lock(phone):
        existing = jobs.find_inflight(ProviderJob.KIND_NUMBER_INFO, phone, wait)
        if existing is not None:
            return CheckTicket(str(existing.id), None)
        job = jobs.create_job(
            kind=ProviderJob.KIND_NUMBER_INFO,
            provider_key=provider_key,
            phone=phone,
            source=_source(user),
            payload={"user_id": user.pk, "sale_id": sale.pk},
        )

    enqueue_failure: dict = {}

    def _enqueue() -> None:
        try:
            run_number_info_job.delay(str(job.id))
        except Exception:  # noqa: BLE001 — broker/redis/OS errors; never leave a job queued
            log.exception("could not enqueue number info job %s", job.id)
            busy = _error(MSG_BUSY)
            jobs.mark_failed(job, busy)
            enqueue_failure["payload"] = busy

    # Outside a transaction this runs immediately, so ``enqueue_failure`` is
    # filled before we return; inside one the failed job is what pollers see.
    transaction.on_commit(_enqueue)
    if enqueue_failure:
        return CheckTicket(None, enqueue_failure["payload"])
    return CheckTicket(str(job.id), None)


def _load_job(job_id: str) -> ProviderJob | None:
    try:
        job = ProviderJob.objects.filter(pk=job_id).first()
    except (ValidationError, ValueError):
        return None
    if job is None or job.kind != ProviderJob.KIND_NUMBER_INFO:
        return None
    return job


def _job_sale(job: ProviderJob) -> Sale | None:
    sale_id = (job.payload or {}).get("sale_id")
    if not sale_id:
        return None
    return (
        Sale.objects.filter(pk=sale_id).select_related("company", "product__line").first()
    )


def check_payload(job_id: str) -> dict:
    """Current JSON for ``job_id``: processing, an error message or the card HTML."""
    job = _load_job(job_id)
    if job is None:
        return _error(MSG_EXPIRED)

    if job.status == ProviderJob.STATUS_FAILED:
        result = job.result or {}
        message = result.get("message")
        return _error(message) if message else _error(MSG_EXPIRED)

    if job.status == ProviderJob.STATUS_DONE:
        result = job.result or {}
        if result.get("status") != "ok" or not isinstance(result.get("info"), dict):
            message = result.get("message")
            return _error(message) if message else _error(MSG_EXPIRED)
        info = NumberInfo.from_dict(result["info"])
        context = card_context(info, _job_sale(job), timezone.localdate())
        return {"status": "ok", "html": render_to_string(CARD_TEMPLATE, context)}

    age = (timezone.now() - job.created_at).total_seconds()
    if age < SystemSettings.get().refresh_wait_seconds:
        return {"status": "processing"}
    return _error(MSG_STILL_RUNNING, final=True)


# --- card -----------------------------------------------------------------


def _num(value) -> str:
    """``6.08``, ``400``, ``0``: no exponent, no trailing zeros; «—» for ``None``."""
    if value is None:
        return DASH
    if isinstance(value, Decimal):
        if not value.is_finite():
            return DASH
        text = format(value.normalize(), "f")
        return "0" if text in ("-0", "") else text
    return str(value)


def _text(value) -> str:
    return (value or "").strip() or DASH


def _date_text(value: date) -> str:
    return value.strftime("%d/%m/%Y")


def _expiry(info: NumberInfo, today: date) -> str:
    if info.ends_at is None:
        return DASH
    ends_on = timezone.localtime(info.ends_at).date()
    days = (ends_on - today).days
    if days < 0:
        tail = "منتهية"
    elif days == 0:
        tail = "تنتهي اليوم"
    else:
        tail = f"متبقي {days} يوم"
    return f"{_date_text(ends_on)} — {tail}"


def _used_of(label: str, used, total, unit: str = "") -> str:
    text = f"{label} {_num(used)}"
    if total is not None:
        text += f" من {_num(total)}"
    return f"{text} {unit}".strip()


def _usage(info: NumberInfo) -> str:
    parts = (
        info.data_used_gb,
        info.data_total_gb,
        info.minutes_used,
        info.minutes_total,
        info.sms_used,
        info.sms_total,
        info.utilization_pct,
    )
    if all(part is None for part in parts):
        return DASH
    pct = DASH if info.utilization_pct is None else f"{_num(info.utilization_pct)}%"
    return " · ".join(
        (
            _used_of("إنترنت", info.data_used_gb, info.data_total_gb, "جيجا"),
            _used_of("دقائق", info.minutes_used, info.minutes_total),
            _used_of("رسائل", info.sms_used, info.sms_total),
            f"نسبة الاستخدام {pct}",
        )
    )


def _sale_price(sale: Sale | None) -> str:
    if sale is None or sale.product is None:
        return DASH
    product = sale.product
    when = _date_text(timezone.localtime(sale.created_at).date())
    return (
        f"{_num(product.default_sell_price)} ₪ — {product.display_name} (آخر بيعة {when})"
    )


def card_context(info: NumberInfo, sale: Sale | None, today: date) -> dict:
    """Display strings for the card partial (pure; never includes ``plan_price``)."""
    return {
        "network": _text(info.network),
        "sim_type": "شريحة إلكترونية" if info.is_esim else "شريحة عادية",
        "status": _text(info.status),
        "plan_name": _text(info.plan_name),
        "expiry": _expiry(info, today),
        "usage": _usage(info),
        "iccid": _text(info.iccid),
        "sale_price": _sale_price(sale),
    }
