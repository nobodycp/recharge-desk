"""Fill ``PhoneLineTracking.sim_identifier`` from provider ``number_info``.

Bulk fetches run as chunked Celery tasks on the ``reports`` queue so the web
request never waits on the provider. Progress lives in Redis for the UI poller.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from django.db.models.functions import Lower, Trim
from django.utils.translation import gettext as _

from providers.contracts import Capability
from providers.redis_conn import get_redis
from providers.service import number_info, service_state
from reports.models import PhoneLineTracking
from reports.stale_phones import normalize_reference_key
from sales.models import Sale

log = logging.getLogger(__name__)

_PER_LINE_BUDGET_SECONDS = 15.0
CHUNK_SIZE = 1  # one provider call per Celery hop
JOB_TTL_SECONDS = 24 * 3600
_KEY_PREFIX = "iccid-fetch"


def _job_key(job_id: str) -> str:
    return f"{_KEY_PREFIX}:{job_id}"


def _queue_key(job_id: str) -> str:
    return f"{_KEY_PREFIX}:{job_id}:queue"


def _active_key(user_id: int) -> str:
    return f"{_KEY_PREFIX}:active:{user_id}"


def _last_sale_for_key(key: str) -> Sale | None:
    return (
        Sale.objects.exclude(status=Sale.Status.CANCELLED)
        .annotate(rk=Lower(Trim("reference_number")))
        .filter(rk=key)
        .select_related("company")
        .order_by("-created_at", "-pk")
        .first()
    )


def fill_one_sim_identifier(key: str, *, user_id: int | None = None) -> str:
    """Process one normalized key. Returns ``filled``, ``skipped``, or ``failed``."""
    key = normalize_reference_key(key)
    if not key:
        return "failed"

    meta = PhoneLineTracking.objects.filter(reference_key=key).first()
    if meta and (meta.sim_identifier or "").strip():
        return "skipped"

    sale = _last_sale_for_key(key)
    if sale is None:
        return "failed"

    company = sale.company
    if service_state(company, Capability.NUMBER_INFO).code != "active":
        return "failed"

    provider_key = (company.provider_key or "").strip().lower()
    phone = (sale.reference_number or "").strip()
    try:
        result = number_info(
            provider_key,
            phone,
            _PER_LINE_BUDGET_SECONDS,
            source="management",
            user_id=user_id,
            sale_id=sale.pk,
        )
    except Exception:  # noqa: BLE001 — one bad line must not abort the batch
        log.exception("iccid fetch crashed for %s", phone)
        return "failed"

    iccid = ((result.info.iccid if result.ok and result.info else "") or "").strip()
    if not iccid:
        return "failed"

    PhoneLineTracking.objects.update_or_create(
        reference_key=key,
        defaults={
            "sim_identifier": iccid[:200],
            "last_display_reference": phone[:64],
        },
    )
    return "filled"


def fill_sim_identifiers_from_provider(keys: list[str], *, user=None) -> dict:
    """Synchronous helper (tests / small scripts). Prefer ``start_iccid_fetch_job``."""
    filled = skipped = failed = 0
    user_id = getattr(user, "pk", None)
    seen: set[str] = set()
    for raw in keys:
        key = normalize_reference_key(raw)
        if not key or key in seen:
            continue
        seen.add(key)
        outcome = fill_one_sim_identifier(key, user_id=user_id)
        if outcome == "filled":
            filled += 1
        elif outcome == "skipped":
            skipped += 1
        else:
            failed += 1
    return {"filled": filled, "skipped": skipped, "failed": failed}


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


def job_status(job_id: str) -> dict | None:
    """Progress snapshot for the poller, or ``None`` if unknown."""
    job_id = (job_id or "").strip()
    if not job_id:
        return None
    raw = get_redis().hgetall(_job_key(job_id))
    if not raw:
        return None
    data = {_decode(k): _decode(v) for k, v in raw.items()}

    def _int(name: str) -> int:
        try:
            return int(data.get(name) or 0)
        except (TypeError, ValueError):
            return 0

    total = _int("total")
    done = _int("done")
    return {
        "job_id": job_id,
        "status": data.get("status") or "unknown",
        "total": total,
        "done": done,
        "remaining": max(0, total - done),
        "filled": _int("filled"),
        "skipped": _int("skipped"),
        "failed": _int("failed"),
        "current": data.get("current") or "",
        "running": (data.get("status") or "") == "running",
        "user_id": data.get("user_id") or "",
    }


def active_job_for_user(user_id: int) -> dict | None:
    job_id = _decode(get_redis().get(_active_key(user_id)))
    if not job_id:
        return None
    status = job_status(job_id)
    if status is None:
        get_redis().delete(_active_key(user_id))
        return None
    return status


def start_iccid_fetch_job(keys: list[str], *, user_id: int) -> str:
    """Enqueue a chunked background fetch. Returns ``job_id``.

    Raises ``ValueError`` when another fetch is already running for this user,
    or when there are no keys to process.
    """
    seen: list[str] = []
    for raw in keys:
        key = normalize_reference_key(raw)
        if key and key not in seen:
            seen.append(key)
    if not seen:
        raise ValueError(_("Select at least one line."))

    redis = get_redis()
    existing_id = _decode(redis.get(_active_key(user_id)))
    if existing_id:
        existing = job_status(existing_id)
        if existing and existing["running"]:
            raise ValueError(
                _(
                    "An ICCID fetch is already running "
                    "(%(done)s/%(total)s done). Wait for it to finish."
                )
                % {"done": existing["done"], "total": existing["total"]}
            )

    job_id = uuid.uuid4().hex
    pipe = redis.pipeline()
    pipe.hset(
        _job_key(job_id),
        mapping={
            "status": "running",
            "total": str(len(seen)),
            "done": "0",
            "filled": "0",
            "skipped": "0",
            "failed": "0",
            "user_id": str(user_id),
            "current": "",
        },
    )
    pipe.rpush(_queue_key(job_id), *seen)
    pipe.expire(_job_key(job_id), JOB_TTL_SECONDS)
    pipe.expire(_queue_key(job_id), JOB_TTL_SECONDS)
    pipe.set(_active_key(user_id), job_id, ex=JOB_TTL_SECONDS)
    pipe.execute()

    from reports.tasks import process_iccid_fetch_chunk

    process_iccid_fetch_chunk.delay(job_id)
    return job_id


def _clear_active(redis, job_id: str, user_id: int | None) -> None:
    if user_id is None:
        return
    active = _decode(redis.get(_active_key(user_id)))
    if active == job_id:
        redis.delete(_active_key(user_id))


def mark_iccid_fetch_failed(job_id: str) -> None:
    redis = get_redis()
    meta_key = _job_key(job_id)
    user_raw = _decode(redis.hget(meta_key, "user_id"))
    redis.hset(meta_key, mapping={"status": "failed", "current": ""})
    try:
        user_id = int(user_raw) if user_raw else None
    except (TypeError, ValueError):
        user_id = None
    _clear_active(redis, job_id, user_id)


def run_iccid_fetch_chunk(job_id: str) -> bool:
    """Process up to ``CHUNK_SIZE`` keys. Returns True if more work remains."""
    redis = get_redis()
    meta_key = _job_key(job_id)
    queue = _queue_key(job_id)
    if not redis.exists(meta_key):
        return False

    user_raw = _decode(redis.hget(meta_key, "user_id"))
    try:
        user_id = int(user_raw) if user_raw else None
    except (TypeError, ValueError):
        user_id = None

    batch: list[str] = []
    for _ in range(CHUNK_SIZE):
        item = redis.lpop(queue)
        if item is None:
            break
        batch.append(_decode(item))

    if not batch:
        redis.hset(meta_key, mapping={"status": "done", "current": ""})
        _clear_active(redis, job_id, user_id)
        return False

    for key in batch:
        redis.hset(meta_key, "current", key)
        outcome = fill_one_sim_identifier(key, user_id=user_id)
        redis.hincrby(meta_key, "done", 1)
        redis.hincrby(meta_key, outcome, 1)

    redis.hset(meta_key, "current", "")
    remaining = int(redis.llen(queue) or 0)
    if remaining <= 0:
        redis.hset(meta_key, "status", "done")
        _clear_active(redis, job_id, user_id)
        return False
    return True
