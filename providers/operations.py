"""Provider operations log: classify an operation's result and record one row.

``classify_*`` are pure (no DB). ``record`` writes one ``ProviderOperation`` and
never raises, so recording can never break the operation it describes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from django.db import transaction

from providers.contracts import (
    AuthRequired,
    ChargeOutcome,
    ChargeResult,
    PermanentError,
    TransientError,
)
from providers.sanitize import scrub

log = logging.getLogger(__name__)

SERVICE_LABELS = {
    "refresh": "التحديث",
    "number_info": "فحص الرقم",
    "charge": "الشحن",
    "check_charge": "متابعة الشحن",
    "reconcile": "المطابقة",
}

OUTCOME_SUCCESS = "success"
OUTCOME_FAILED = "failed"
OUTCOME_PENDING = "pending"

OUTCOME_LABELS = {
    OUTCOME_SUCCESS: "نجحت",
    OUTCOME_FAILED: "فشلت",
    OUTCOME_PENDING: "قيد المتابعة",
}

SOURCE_LABELS = {
    "web": "الصفحة العامة",
    "sms": "SMS",
    "api": "API",
    "employee": "الموظف",
    "management": "الإدارة",
    "system": "مهمة تلقائية",
}
SOURCES = tuple(SOURCE_LABELS)

CAUSE_LABELS = {
    "login_failed": "خطأ تسجيل الدخول",
    "login_busy": "تسجيل الدخول قيد التنفيذ، حاول بعد قليل",
    "service_off": "الخدمة موقوفة",
    "not_configured": "بيانات الحساب ناقصة",
    "connection": "تعذّر الاتصال بالمزوّد",
    "rejected": "رفض المزوّد",
    "provider_error": "خطأ في العملية",
    "price_mismatch": "السعر غير مطابق",
    "not_found": "الرقم غير موجود لدى المزوّد",
    "internal": "خطأ داخلي",
}

SUCCESS_LABELS = {
    "refresh": "تمّ التحديث",
    "number_info": "تمّ فحص الرقم",
    "charge": "تمّ الشحن",
    "check_charge": "تمّت متابعة الشحن",
    "reconcile": "تمّت المطابقة",
}

PENDING_MESSAGE = "الشحن قيد المتابعة"
NOT_SENT_PREFIX = "not_sent:"

_CODE_TO_CAUSE = {
    "login_failed": "login_failed",
    "login_error": "login_failed",
    "otp_required": "login_failed",
    "auth_required": "login_failed",
    # AuthRequired texts that persist after a login (sky adapter, refresh path).
    "session_expired": "login_failed",
    "session not ready": "login_failed",
    "login_busy": "login_busy",
    "provider_disabled": "service_off",
    "config_error": "not_configured",
    "not_configured": "not_configured",
    "connection_error": "connection",
    "proxy_error": "connection",
    "session_error": "connection",
    "sync_budget_exceeded": "connection",
    "timeout": "connection",
    "context_error": "internal",
    "unknown_provider": "internal",
}

_REJECT_REASONS = {
    "not_found": "الرقم غير موجود",
    "not_active": "الرقم غير مفعّل",
    "not_eligible": "الرقم غير مؤهّل",
}

MESSAGE_MAX = 255
DETAIL_MAX = 500


@dataclass(frozen=True)
class Classified:
    outcome: str
    error_code: str
    message: str


def _label(service: str) -> str:
    return SERVICE_LABELS.get(service, service)


def _failed(service: str, error_code: str, cause: str) -> Classified:
    return Classified(OUTCOME_FAILED, error_code, f"فشل {_label(service)} — {cause}")


def success(service: str, ref: str = "") -> Classified:
    message = SUCCESS_LABELS.get(service) or f"تم {_label(service)}"
    if ref:
        message += f" — رقم العملية {ref}"
    return Classified(OUTCOME_SUCCESS, "", message)


def rejected(service: str, reason: str) -> Classified:
    return _failed(service, "rejected", f"{CAUSE_LABELS['rejected']}: {reason}")


def not_found(service: str) -> Classified:
    """The provider has no such line (number check). Refresh keeps its reject reason."""
    return _failed(service, "not_found", CAUSE_LABELS["not_found"])


def classify_code(service: str, code: str) -> Classified:
    """Map a provider/gateway error code (optionally ``not_sent:<code>``) to a failure."""
    code = (code or "").strip()
    if code.startswith(NOT_SENT_PREFIX):
        code = code[len(NOT_SENT_PREFIX):].strip()
    if code in _REJECT_REASONS:
        return rejected(service, _REJECT_REASONS[code])
    error_code = _CODE_TO_CAUSE.get(code)
    if error_code is None:
        return _failed(service, "provider_error", f"خطأ في عملية {_label(service)}")
    return _failed(service, error_code, CAUSE_LABELS[error_code])


def classify_exception(service: str, exc: BaseException) -> Classified:
    if isinstance(exc, AuthRequired):
        return classify_code(service, "login_failed")
    if isinstance(exc, (TransientError, PermanentError)):
        return classify_code(service, str(exc))
    return _failed(service, "internal", CAUSE_LABELS["internal"])


def classify_charge(service: str, outcome: ChargeOutcome) -> Classified:
    """``service`` is ``charge`` or ``check_charge``."""
    if outcome.result == ChargeResult.SUCCESS:
        return success(service, outcome.provider_ref)
    if outcome.result == ChargeResult.UNVERIFIED:
        return Classified(OUTCOME_PENDING, "", PENDING_MESSAGE)
    return classify_code(service, outcome.message)


def record(
    provider_key: str,
    service: str,
    classified: Classified,
    *,
    phone: str = "",
    source: str = "system",
    user_id: int | None = None,
    sale_id: int | None = None,
    job_id=None,
    attempts: int = 1,
    duration_ms: int | None = None,
    detail: str = "",
) -> None:
    """Write one operation row; never raises and never poisons the caller's transaction."""
    from providers.models import ProviderOperation

    # Postgres rejects overlong values (SQLite doesn't), so clip to the column.
    phone = (phone or "")[: ProviderOperation._meta.get_field("phone").max_length]
    outcome = classified.outcome
    if source not in SOURCES:
        log.warning(
            "provider operation %s/%s: unknown source %r, storing 'system'",
            provider_key,
            service,
            source,
        )
        source = "system"
    if outcome not in OUTCOME_LABELS:
        log.warning(
            "provider operation %s/%s: unknown outcome %r, storing 'failed'",
            provider_key,
            service,
            outcome,
        )
        outcome = OUTCOME_FAILED
    try:
        with transaction.atomic():
            ProviderOperation.objects.create(
                provider_key=provider_key,
                service=service,
                phone=phone,
                outcome=outcome,
                error_code=classified.error_code,
                message=classified.message[:MESSAGE_MAX],
                source=source,
                user_id=user_id,
                sale_id=sale_id,
                job_id=job_id,
                attempts=attempts,
                duration_ms=duration_ms,
                detail=scrub(detail or "")[:DETAIL_MAX],
            )
    except Exception as exc:
        log.warning(
            "provider operation not recorded (%s/%s): %s",
            provider_key,
            service,
            scrub(str(exc)),
        )
