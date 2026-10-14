"""Inbound SMS processing: guards, number extraction, refresh, reply queueing."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from sms_gateway.models import (
    InboundSms,
    OutboundSms,
    SmsAccessRule,
    SmsGatewaySettings,
    SmsReplyPolicy,
)
from sms_gateway.services.refresh_client import call_refresh_api, uses_in_process_engine
from sms_gateway.validation import extract_number

log = logging.getLogger(__name__)

# Refresh status codes that are not real outcomes but still may want a reply.
INVALID_NUMBER_STATUS = "not_found"


@dataclass
class ProcessResult:
    state: str
    reply_text: str | None = None
    extracted_number: str | None = None
    refresh_status: str | None = None
    inbound_id: int | None = None
    outbound_id: int | None = None
    notes: list[str] = field(default_factory=list)


def _normalize(number: str) -> str:
    return (number or "").strip()


def _is_blocked(from_number: str, settings_obj: SmsGatewaySettings) -> bool:
    rules = list(SmsAccessRule.objects.filter(is_active=True))
    blocks = [r.value for r in rules if r.mode == SmsAccessRule.Mode.BLOCK]
    allows = [r.value for r in rules if r.mode == SmsAccessRule.Mode.ALLOW]

    def _matches(values: list[str]) -> bool:
        for v in values:
            v = (v or "").strip()
            if v and (from_number == v or from_number.startswith(v)):
                return True
        return False

    if _matches(blocks):
        return True
    if settings_obj.allowlist_mode and not _matches(allows):
        return True
    return False


def _sender_over_limit(from_number: str, settings_obj: SmsGatewaySettings) -> bool:
    if not settings_obj.sender_max_messages:
        return False
    window_start = timezone.now() - timedelta(minutes=settings_obj.sender_window_minutes or 60)
    # Counts the just-stored row too, so use ``>`` to allow exactly
    # ``sender_max_messages`` within the window and block the next one.
    count = InboundSms.objects.filter(
        from_number=from_number,
        received_at__gte=window_start,
    ).exclude(state=InboundSms.State.DUPLICATE).count()
    return count > settings_obj.sender_max_messages


def _global_cap_reached(settings_obj: SmsGatewaySettings) -> bool:
    if not settings_obj.global_daily_cap:
        return False
    start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    sent_today = OutboundSms.objects.filter(created_at__gte=start).count()
    return sent_today >= settings_obj.global_daily_cap


def _reply_for(status_code: str, default_title: str, default_body: str,
               settings_obj: SmsGatewaySettings) -> str | None:
    """Resolve the reply text for a refresh status, honoring policy switches.

    Returns None when no reply should be sent.
    """
    if not settings_obj.replies_master_enabled:
        return None
    policy = (
        SmsReplyPolicy.objects.select_related("status")
        .filter(status__code=status_code)
        .first()
    )
    if policy is not None and not policy.reply_enabled:
        return None
    if policy is not None and policy.message_override.strip():
        return policy.message_override.strip()
    parts = [p for p in [default_title.strip(), default_body.strip()] if p]
    text = "\n".join(parts).strip()
    return text or None


_ERROR_TITLE = "خطأ"
_ERROR_BODY = "تعذّر تنفيذ التحديث حالياً. يرجى المحاولة لاحقاً."


def _queue_reply(inbound: InboundSms, reply: str | None, s: SmsGatewaySettings) -> int | None:
    """Create the single reply ``OutboundSms`` for ``inbound``; returns its id."""
    if not reply:
        return None
    return OutboundSms.objects.create(
        to_number=inbound.from_number,
        body=reply,
        related_inbound=inbound,
        max_attempts=s.max_send_attempts or 3,
    ).pk


def complete_inbound(inbound_id: int, status_code: str, title: str, body: str) -> None:
    """Finish an inbound SMS whose refresh job just completed.

    Called from the refresh task's completion hook (and for immediate
    payloads). Idempotent and race-safe: the inbound is claimed with a
    conditional ``refreshing`` → ``processed`` update, so only one caller
    (worker, reaper, or a late duplicate) ever creates the reply.
    """
    s = SmsGatewaySettings.load()
    reply = _reply_for(status_code or "error", title or "", body or "", s)
    # Global daily cap: if reached, do not queue a new reply.
    if reply and _global_cap_reached(s):
        reply = None

    with transaction.atomic():
        claimed = InboundSms.objects.filter(
            pk=inbound_id, state=InboundSms.State.REFRESHING
        ).update(
            state=InboundSms.State.PROCESSED,
            reply_text=reply or "",
            processed_at=timezone.now(),
            delete_requested=True,
        )
        if claimed != 1:
            return
        _queue_reply(InboundSms.objects.get(pk=inbound_id), reply, s)


def _refresh_async(inbound: InboundSms, number: str) -> ProcessResult:
    """Submit the refresh as a job; the reply is queued when it completes."""
    from phone_refresh.models import RefreshSource
    from phone_refresh.services import refresh_jobs

    # Mark the inbound first: with an eager/fast worker the completion hook
    # can fire before ``submit_refresh`` even returns.
    InboundSms.objects.filter(pk=inbound.pk).update(
        state=InboundSms.State.REFRESHING, extracted_number=number
    )
    payload = None
    try:
        ticket = refresh_jobs.submit_refresh(
            number,
            ip=None,
            source=RefreshSource.SMS,
            notify={"sms_inbound_id": inbound.pk},
        )
        # A ticket carrying a payload is always immediate (validation,
        # cooldown, enqueue failure, ...), whether or not it has a job id.
        payload = ticket.payload
    except Exception:  # noqa: BLE001 — never leave the inbound stuck in refreshing
        log.exception("could not submit refresh for inbound SMS %s", inbound.pk)
        payload = {
            "status": "error",
            "message": {"title": _ERROR_TITLE, "body": _ERROR_BODY},
        }

    status_code = None
    if payload is not None:
        message = payload.get("message") or {}
        status_code = payload.get("status") or "error"
        complete_inbound(
            inbound.pk, status_code, message.get("title") or "", message.get("body") or ""
        )

    inbound.refresh_from_db()
    outbound_id = (
        OutboundSms.objects.filter(related_inbound=inbound)
        .values_list("pk", flat=True)
        .first()
    )
    return ProcessResult(
        state=inbound.state,
        reply_text=inbound.reply_text or None,
        extracted_number=number,
        refresh_status=status_code,
        inbound_id=inbound.pk,
        outbound_id=outbound_id,
    )


def process_inbound(
    *,
    from_number: str,
    raw_text: str,
    device=None,
    device_msg_id: str = "",
    request=None,
    simulate: bool = False,
) -> ProcessResult:
    """Full inbound pipeline. When ``simulate`` is True nothing is persisted
    and no reply is queued — the computed reply is returned for preview."""
    s = SmsGatewaySettings.load()
    from_number = _normalize(from_number)
    device_msg_id = (device_msg_id or "").strip()

    # Duplicate guard (real traffic only).
    if not simulate and device is not None and device_msg_id:
        existing = InboundSms.objects.filter(
            device=device, device_msg_id=device_msg_id
        ).first()
        if existing is not None:
            return ProcessResult(state=InboundSms.State.DUPLICATE, inbound_id=existing.pk)

    inbound = None
    if not simulate:
        inbound = InboundSms.objects.create(
            device=device,
            from_number=from_number,
            raw_text=raw_text or "",
            device_msg_id=device_msg_id,
            state=InboundSms.State.RECEIVED,
        )

    def finish(state: str, reply: str | None, *, extracted="", status_code=None,
               refresh_log=None) -> ProcessResult:
        outbound_id = None
        if not simulate and inbound is not None:
            inbound.state = state
            inbound.extracted_number = extracted or ""
            inbound.reply_text = reply or ""
            inbound.refresh_log = refresh_log
            inbound.processed_at = timezone.now()
            if state in {InboundSms.State.PROCESSED, InboundSms.State.TEST}:
                inbound.delete_requested = True
            inbound.save()
            outbound_id = _queue_reply(inbound, reply, s)
        return ProcessResult(
            state=state,
            reply_text=reply,
            extracted_number=extracted or None,
            refresh_status=status_code,
            inbound_id=inbound.pk if inbound else None,
            outbound_id=outbound_id,
        )

    # Block / allow list.
    if _is_blocked(from_number, s):
        return finish(InboundSms.State.BLOCKED, None)

    # Per-sender rate limit (count the just-stored row too via >= check above).
    if not simulate and _sender_over_limit(from_number, s):
        return finish(InboundSms.State.RATE_LIMITED, None)

    # Test number → health reply, no real refresh.
    if s.test_number and from_number == s.test_number.strip():
        reply = s.test_reply_message.strip() if s.replies_master_enabled else None
        return finish(InboundSms.State.TEST, reply)

    # Service off.
    if not s.service_enabled:
        reply = None
        if s.send_service_off_reply and s.replies_master_enabled:
            reply = s.service_off_message.strip() or None
        return finish(InboundSms.State.IGNORED, reply)

    # Extract number.
    number = extract_number(raw_text)
    if not number:
        reply = _reply_for(INVALID_NUMBER_STATUS, "", "الرجاء إرسال رقم صحيح يبدأ بـ 050-055.", s)
        return finish(InboundSms.State.IGNORED, reply)

    # Internal engine: run as a job so the gateway's HTTP call returns at once.
    if (
        not simulate
        and inbound is not None
        and settings.REFRESH_ASYNC_ENABLED
        and uses_in_process_engine(s)
    ):
        return _refresh_async(inbound, number)

    # Refresh via configured API gateway.
    outcome = call_refresh_api(number, settings_obj=s, request=request)
    reply = _reply_for(outcome.status_code, outcome.title, outcome.body, s)

    # Global daily cap: if reached, do not queue a new reply.
    if reply and not simulate and _global_cap_reached(s):
        reply = None

    return finish(
        InboundSms.State.PROCESSED,
        reply,
        extracted=number,
        status_code=outcome.status_code,
    )
