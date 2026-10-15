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
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone
from redis.exceptions import LockError

from accounts.permissions import is_management
from companies.models import ProductLine
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


def _now() -> datetime:
    """Current local time (one seam, so the card's "today" can be pinned in tests)."""
    return timezone.localtime()


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
        now = _now()
        context = card_context(info, _job_sale(job), now.date(), phone=job.phone, now=now)
        context.update(_branding())
        return {"status": "ok", "html": render_to_string(CARD_TEMPLATE, context)}

    age = (timezone.now() - job.created_at).total_seconds()
    if age < SystemSettings.get().refresh_wait_seconds:
        return {"status": "processing"}
    return _error(MSG_STILL_RUNNING, final=True)


# --- card -----------------------------------------------------------------

DEFAULT_SITE_NAME = "Pro Sim"


def _branding() -> dict:
    """Site logo (light variant) and name for the share image's header and footer."""
    try:
        from core.models import SiteBranding

        branding = SiteBranding.load()
        logo = branding.logo.url if branding.logo else ""
        name = (branding.site_name or "").strip()
    except Exception:  # noqa: BLE001 — branding must never break a result card
        log.warning("site branding unavailable for the number check card", exc_info=True)
        logo, name = "", ""
    return {"site_logo": logo, "site_name": name or DEFAULT_SITE_NAME}


# Keyword (lower-case) -> brand tone class of the card header.
_NETWORK_TONES = (
    (("سلكوم", "سيلكوم", "cellcom"), "cellcom"),
    (("بليفون", "بلفون", "pelephone"), "pelephone"),
    (("بارتنر", "partner", "اورنج", "أورنج", "orange"), "partner"),
    (("هوت", "hot"), "hot"),
    (("جولان", "golan"), "golan"),
    (("جوال", "jawwal"), "jawwal"),
    (("وطنية", "اوريدو", "أوريدو", "ooredoo"), "ooredoo"),
)
_INACTIVE_WORDS = ("غير", "موقوف", "معلق", "ملغ", "inactive", "suspend", "blocked")
_ACTIVE_WORDS = ("مفعل", "فعال", "نشط", "active")
_GAUGES = (
    ("data", "إنترنت", "جيجا"),
    ("minutes", "دقائق", "دقيقة"),
    ("sms", "رسائل", "رسالة"),
)
_HUNDRED = Decimal(100)


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


def _date_text(value: date) -> str:
    return value.strftime("%d/%m/%Y")


def _pct_text(value: Decimal | None) -> str:
    """Whole percent from 10 up, one decimal below: ``1.5``, ``0``, ``73``."""
    if value is None:
        return ""
    step = Decimal("1") if value >= 10 else Decimal("0.1")
    return _num(value.quantize(step))


def _network_tone(network: str) -> str:
    text = network.lower()
    for words, tone in _NETWORK_TONES:
        if any(word in text for word in words):
            return tone
    return "default"


def _status_tone(status: str) -> str:
    text = status.lower()
    if not text:
        return "muted"
    if any(word in text for word in _INACTIVE_WORDS):
        return "warn"
    if any(word in text for word in _ACTIVE_WORDS):
        return "ok"
    return "muted"


def _days_word(days: int) -> str:
    return "أيام" if 3 <= days <= 10 else "يوم"


def _expiry(info: NumberInfo, today: date) -> dict | None:
    if info.ends_at is None:
        return None
    ends_on = timezone.localtime(info.ends_at).date()
    days = (ends_on - today).days
    expiry = {"date": _date_text(ends_on), "days": "", "word": "", "state": ""}
    if days < 0:
        return {**expiry, "state": "منتهية", "tone": "danger"}
    if days == 0:
        return {**expiry, "state": "تنتهي اليوم", "tone": "danger"}
    tone = "danger" if days <= 3 else "warn" if days <= 7 else "ok"
    return {**expiry, "days": str(days), "word": _days_word(days), "tone": tone}


def _gauge(key: str, label: str, unit: str, used, total) -> dict:
    gauge = {
        "key": key,
        "label": label,
        "unit": unit,
        "used": _num(used),
        "total": "" if total is None else _num(total),
        "pct": "",
        "remaining": "",
        "tone": "ok",
        "included": total != 0,
    }
    if used is None or not total:
        return gauge
    used, total = Decimal(used), Decimal(total)
    pct = min(max(used / total * _HUNDRED, Decimal(0)), _HUNDRED)
    gauge.update(
        pct=_pct_text(pct),
        remaining=_num(max(total - used, Decimal(0))),
        tone="danger" if pct >= 90 else "warn" if pct >= 70 else "ok",
    )
    return gauge


def _usage(info: NumberInfo) -> list[dict]:
    values = {
        "data": (info.data_used_gb, info.data_total_gb),
        "minutes": (info.minutes_used, info.minutes_total),
        "sms": (info.sms_used, info.sms_total),
    }
    return [_gauge(key, label, unit, *values[key]) for key, label, unit in _GAUGES]


def _iccid_groups(iccid: str) -> str:
    return " ".join(iccid[i : i + 4] for i in range(0, len(iccid), 4))


def _sale(sale: Sale | None) -> dict | None:
    if sale is None or sale.product is None:
        return None
    return {
        "price": _num(sale.product.default_sell_price),
        "product": sale.product.display_name,
        "date": _date_text(timezone.localtime(sale.created_at).date()),
    }


def _phone_display(phone: str) -> str:
    """``0521234567`` -> ``052-123-4567``; anything else is shown as typed."""
    if len(phone) == 10 and phone.isascii() and phone.isdigit():
        return f"{phone[:3]}-{phone[3:6]}-{phone[6:]}"
    return phone


MIN_CONTAINED_NAME = 3  # shorter names are too ambiguous to match by containment
_WORD = re.compile(r"\w+")


def _normalise_line_name(text: str) -> str:
    return "".join(ch for ch in (text or "").lower() if ch not in " -_\u00a0")


def _words(text: str) -> list[str]:
    return _WORD.findall((text or "").lower())


def _tone_matches(network: str, line_name: str) -> bool:
    """Same brand keyword (spaces and hyphens ignored): «Cell-com 019» is a Cellcom line."""
    tone = _network_tone(_normalise_line_name(network))
    return tone != "default" and tone == _network_tone(_normalise_line_name(line_name))


def _name_contained(network: str, line_name: str) -> bool:
    """One name's words appear, as whole consecutive words, in the other's."""
    a, b = _words(network), _words(line_name)
    shorter, longer = sorted((a, b), key=lambda w: len("".join(w)))
    if len("".join(shorter)) < MIN_CONTAINED_NAME:
        return False
    return any(
        longer[i : i + len(shorter)] == shorter
        for i in range(len(longer) - len(shorter) + 1)
    )


def _line_logo(sale: Sale | None, network: str) -> tuple[str, str]:
    """(logo URL, line name): the last sale's line icon, else a line matching the network.

    Brand-keyword matches rank before whole-word name containment.
    """
    product = sale.product if sale is not None else None
    line = product.line if product is not None else None
    if line is not None and line.icon:
        return line.icon.url, line.name
    if not network:
        return "", ""
    candidates = list(
        ProductLine.objects.exclude(icon__isnull=True)
        .exclude(icon="")
        .order_by("sort_order", "pk")
    )
    for matches in (_tone_matches, _name_contained):
        for candidate in candidates:
            if matches(network, candidate.name):
                return candidate.icon.url, candidate.name
    return "", ""


def _share(ctx: dict) -> dict:
    """What the shared image may show: customer facts only, never price, ICCID or sale."""
    return {
        "phone_display": ctx["phone_display"],
        "network": ctx["network"],
        "sim_type": ctx["sim_type"],
        "status": ctx["status"],
        "status_tone": ctx["status_tone"],
        "plan_name": ctx["plan_name"],
        "expiry": ctx["expiry"],
        "usage": [
            {key: g[key] for key in ("label", "used", "total", "unit", "pct", "included")}
            for g in ctx["usage"]
        ],
        "utilization": ctx["utilization"],
        "line_logo": ctx["line_logo"],
        "checked_at": ctx["checked_at"],
    }


def card_context(
    info: NumberInfo, sale: Sale | None, today: date, phone: str = "", now=None
) -> dict:
    """Display values for the card partial (never includes ``plan_price``).

    ``phone`` is the checked number; ``now`` (aware) stamps the share image.
    """
    network = (info.network or "").strip()
    status = (info.status or "").strip()
    iccid = (info.iccid or "").strip()
    phone = (phone or "").strip()
    usage = _usage(info)
    line_logo, line_name = _line_logo(sale, network)
    ctx = {
        "phone": phone,
        "phone_display": _phone_display(phone),
        "network": network,
        "network_tone": _network_tone(network),
        "line_logo": line_logo,
        "line_name": line_name,
        "is_esim": info.is_esim,
        "sim_type": "شريحة إلكترونية" if info.is_esim else "شريحة عادية",
        "status": status,
        "status_tone": _status_tone(status),
        "plan_name": (info.plan_name or "").strip(),
        "expiry": _expiry(info, today),
        "usage": usage,
        "has_usage": info.utilization_pct is not None
        or any(g["used"] != DASH or g["total"] for g in usage),
        "utilization": _pct_text(info.utilization_pct),
        "iccid": iccid,
        "iccid_groups": _iccid_groups(iccid),
        "sale": _sale(sale),
        "checked_at": timezone.localtime(now).strftime("%d/%m/%Y · %H:%M") if now else "",
    }
    ctx["share"] = _share(ctx)
    return ctx
