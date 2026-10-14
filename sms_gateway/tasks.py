"""Celery tasks for the SMS gateway."""
from __future__ import annotations

from celery import shared_task

from sms_gateway.services.processing import sweep_stuck_inbound


@shared_task
def sweep_stuck_inbound_sms() -> int:
    """Finish inbound SMS stuck in ``refreshing`` (beat, every 5 min); returns count."""
    return sweep_stuck_inbound()
