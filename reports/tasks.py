"""Background work for management reports (ICCID bulk fetch, …)."""
from __future__ import annotations

import logging

from celery import shared_task

log = logging.getLogger(__name__)

# One provider call (~15s) plus slack; chain continues via re-enqueue.
_CHUNK_SOFT = 45
_CHUNK_HARD = 60


@shared_task(
    soft_time_limit=_CHUNK_SOFT,
    time_limit=_CHUNK_HARD,
    name="reports.tasks.process_iccid_fetch_chunk",
)
def process_iccid_fetch_chunk(job_id: str) -> None:
    """Fetch ICCID for the next chunk, then enqueue the rest if needed."""
    from reports.iccid_fetch import mark_iccid_fetch_failed, run_iccid_fetch_chunk

    try:
        more = run_iccid_fetch_chunk(job_id)
    except Exception:  # noqa: BLE001 — mark job failed, never crash the worker loop
        log.exception("iccid fetch chunk failed for job %s", job_id)
        mark_iccid_fetch_failed(job_id)
        return
    if more:
        process_iccid_fetch_chunk.delay(job_id)
