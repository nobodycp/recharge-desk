"""Helpers for writing audit events from the service layer.

Service code calls :func:`record` whenever it commits a meaningful
mutation. The helper is deliberately tolerant — bad data, a missing
actor, or even an exception serialising the diff must never bubble out
and abort the user's action; auditing is observation, not enforcement.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Mapping, Optional

from django.db import models

from audit.models import AuditLog

logger = logging.getLogger(__name__)


def _coerce(v: Any):
    """Make ``v`` JSON-safe without losing precision for money values."""
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, models.Model):
        return f"{v._meta.label}#{v.pk}"
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return str(v)


def diff_fields(before: Optional[Mapping[str, Any]], after: Mapping[str, Any]) -> dict:
    """Return ``{field: {old, new}}`` for fields whose value changed.

    ``before is None`` produces the full snapshot — handy for ``CREATE``
    events where we want to see what was inserted.
    """
    out: dict[str, dict] = {}
    if before is None:
        for k, v in after.items():
            out[k] = {"old": None, "new": _coerce(v)}
        return out
    for k, new in after.items():
        old = before.get(k, None)
        if old != new:
            out[k] = {"old": _coerce(old), "new": _coerce(new)}
    return out


def _client_ip(request) -> Optional[str]:
    if request is None:
        return None
    fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if fwd:
        return fwd.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None


def record(
    action: str,
    instance,
    *,
    actor=None,
    request=None,
    changes: Optional[Mapping[str, Any]] = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> Optional[AuditLog]:
    """Write a single audit row.

    ``instance`` may be a Django model (preferred) or any object that
    responds to ``str(...)`` and has a ``pk``; we degrade gracefully if
    introspection fails. Returns the created row, or ``None`` when the
    caller passed bad input — we'd rather lose one log entry than break
    a sale.
    """
    try:
        if hasattr(instance, "_meta") and hasattr(instance._meta, "label_lower"):
            label = instance._meta.label_lower
        else:
            label = type(instance).__name__.lower()

        oid = getattr(instance, "pk", None)
        repr_ = ""
        try:
            repr_ = str(instance)[:255]
        except Exception:
            repr_ = ""

        payload = dict(changes or {})
        if extra:
            payload.setdefault("_extra", {}).update({k: _coerce(v) for k, v in extra.items()})

        if actor is None and request is not None:
            actor = getattr(request, "user", None)
            if actor is not None and not getattr(actor, "is_authenticated", False):
                actor = None

        return AuditLog.objects.create(
            actor=actor,
            action=action,
            model_label=label,
            object_id="" if oid is None else str(oid),
            object_repr=repr_,
            changes=payload,
            ip=_client_ip(request),
        )
    except Exception:
        # Auditing failure is never user-visible; log it for ops.
        logger.exception("audit.record failed: action=%s instance=%r", action, instance)
        return None


def snapshot(instance, fields) -> dict:
    """Capture ``{field: value}`` for the given instance fields.

    Used by callers that want to compute a diff after mutating the
    object: ``before = snapshot(s, FIELDS); … s.save(); diff = diff_fields(before, snapshot(s, FIELDS))``.
    """
    out = {}
    for f in fields:
        out[f] = _coerce(getattr(instance, f, None))
    return out


__all__ = ["record", "diff_fields", "snapshot"]
