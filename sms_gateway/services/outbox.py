"""Outbox claim / delivery / failover helpers for gateway devices."""
from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from sms_gateway.models import InboundSms, OutboundSms, SmsGatewaySettings, SmsGatewayDevice


def _reclaim_expired(settings_obj: SmsGatewaySettings) -> None:
    """Return claimed-but-unconfirmed replies to the queue after the timeout."""
    timeout = settings_obj.claim_timeout_seconds or 120
    cutoff = timezone.now() - timedelta(seconds=timeout)
    OutboundSms.objects.filter(
        state=OutboundSms.State.CLAIMED,
        claimed_at__lt=cutoff,
    ).update(state=OutboundSms.State.QUEUED, claimed_by=None, claimed_at=None)


def _device_daily_remaining(device: SmsGatewayDevice) -> int | None:
    """Remaining sends today for ``device``; None = unlimited."""
    if not device.daily_send_limit:
        return None
    device.roll_daily_counter()
    return max(0, device.daily_send_limit - device.sent_today)


def claim_for_device(device: SmsGatewayDevice, limit: int = 10) -> list[OutboundSms]:
    """Atomically claim up to ``limit`` queued replies for a sendable device."""
    settings_obj = SmsGatewaySettings.load()
    if not device.is_sendable:
        return []

    remaining = _device_daily_remaining(device)
    if remaining is not None:
        if remaining <= 0:
            return []
        limit = min(limit, remaining)

    claimed: list[OutboundSms] = []
    with transaction.atomic():
        _reclaim_expired(settings_obj)
        rows = list(
            OutboundSms.objects.select_for_update(skip_locked=True)
            .filter(state=OutboundSms.State.QUEUED)
            .order_by("created_at", "id")[:limit]
        )
        now = timezone.now()
        for row in rows:
            row.state = OutboundSms.State.CLAIMED
            row.claimed_by = device
            row.claimed_at = now
            row.attempts = row.attempts + 1
            row.save(update_fields=["state", "claimed_by", "claimed_at", "attempts"])
            claimed.append(row)
    return claimed


def pending_delete_ids(device: SmsGatewayDevice, limit: int = 100) -> list[str]:
    """Device message ids the server wants this device to delete locally."""
    qs = (
        InboundSms.objects.filter(
            device=device,
            delete_requested=True,
            delete_confirmed_at__isnull=True,
        )
        .exclude(device_msg_id="")
        .values_list("device_msg_id", flat=True)[:limit]
    )
    return list(qs)


def mark_delivery(
    device: SmsGatewayDevice,
    *,
    sent_ids: list[int],
    failed: list[dict],
    deleted_device_msg_ids: list[str],
) -> dict:
    """Apply a device's delivery report: sent / failed / deletions."""
    settings_obj = SmsGatewaySettings.load()
    now = timezone.now()
    result = {"sent": 0, "failed": 0, "deleted": 0}

    if sent_ids:
        sent_rows = list(
            OutboundSms.objects.filter(pk__in=sent_ids).exclude(state=OutboundSms.State.SENT)
        )
        for row in sent_rows:
            row.state = OutboundSms.State.SENT
            row.sent_at = now
            row.error = ""
            row.save(update_fields=["state", "sent_at", "error"])
        result["sent"] = len(sent_rows)
        device.roll_daily_counter()
        device.sent_today += len(sent_rows)
        device.consecutive_failures = 0
        device.auto_paused_at = None
        device.save(
            update_fields=["sent_today", "sent_today_date", "consecutive_failures", "auto_paused_at"]
        )

    if failed:
        failed_ids = [f.get("id") for f in failed if f.get("id") is not None]
        rows = list(OutboundSms.objects.filter(pk__in=failed_ids))
        error_by_id = {f.get("id"): str(f.get("error") or "")[:300] for f in failed}
        for row in rows:
            row.error = error_by_id.get(row.pk, "")
            if row.attempts >= (row.max_attempts or settings_obj.max_send_attempts or 3):
                row.state = OutboundSms.State.DEAD_LETTER
            else:
                row.state = OutboundSms.State.QUEUED
                row.claimed_by = None
                row.claimed_at = None
            row.save(update_fields=["error", "state", "claimed_by", "claimed_at"])
        result["failed"] = len(rows)
        # Track consecutive failures for auto-pause.
        device.consecutive_failures += len(rows)
        if (
            settings_obj.auto_pause_threshold
            and device.consecutive_failures >= settings_obj.auto_pause_threshold
            and device.auto_paused_at is None
        ):
            device.auto_paused_at = now
        device.save(update_fields=["consecutive_failures", "auto_paused_at"])

    if deleted_device_msg_ids:
        updated = InboundSms.objects.filter(
            device=device,
            device_msg_id__in=deleted_device_msg_ids,
            delete_confirmed_at__isnull=True,
        ).update(delete_confirmed_at=now)
        result["deleted"] = updated

    return result
