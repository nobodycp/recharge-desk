"""Bearer-token verification shared by every endpoint that takes an ``ApiToken``."""
from __future__ import annotations

import datetime as dt
import hashlib

from django.db.models import Q
from django.utils import timezone

from phone_refresh.models import ApiToken


def extract_bearer_token(request) -> str | None:
    """Raw token from ``Authorization: Bearer <token>``, or ``None``."""
    header = request.META.get("HTTP_AUTHORIZATION") or ""
    if not header.lower().startswith("bearer "):
        return None
    return header[7:].strip() or None


def resolve_token(raw_token: str) -> ApiToken | None:
    """Active token matching ``raw_token``, looked up by its sha256 digest."""
    digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    try:
        token = ApiToken.objects.get(token_hash=digest)
    except ApiToken.DoesNotExist:
        return None
    return token if token.is_active else None


LAST_USED_RESOLUTION = dt.timedelta(seconds=60)


def touch_token(token: ApiToken) -> None:
    """Audit trail: stamp ``last_used_at`` (at most once a minute per token).

    One conditional UPDATE, so busy tokens don't write on every request and
    concurrent requests can't race each other.
    """
    now = timezone.now()
    ApiToken.objects.filter(pk=token.pk).filter(
        Q(last_used_at__isnull=True) | Q(last_used_at__lt=now - LAST_USED_RESOLUTION)
    ).update(last_used_at=now)
