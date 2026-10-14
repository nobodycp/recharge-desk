"""Synchronous provider API used by the rest of the project."""
from __future__ import annotations

import logging
import time

from providers.contracts import (
    ChargeContext,
    ChargeOutcome,
    ChargeResult,
    PermanentError,
    ProviderError,
    RawResponse,
    TransientError,
)
from providers.registry import get_adapter
from providers.sanitize import scrub
from providers.sessions import run_authenticated

log = logging.getLogger(__name__)

REFRESH_ATTEMPTS = 3
REFRESH_RETRY_DELAYS = (1, 2)


def _error_text(exc: Exception) -> str:
    return scrub(str(exc)) or exc.__class__.__name__


def _error_response(error: str) -> RawResponse:
    return RawResponse(text="", json=None, status_code=0, error=error)


def refresh(provider_key: str, phone: str) -> RawResponse:
    """Refresh ``phone``; transient failures are retried, errors become a RawResponse.

    Raises ``KeyError`` for an unknown ``provider_key``.
    """
    adapter = get_adapter(provider_key)
    error = ""
    for attempt in range(REFRESH_ATTEMPTS):
        try:
            return run_authenticated(adapter, lambda: adapter.refresh(phone))
        except TransientError as exc:
            error = _error_text(exc)
            log.warning(
                "provider %s refresh attempt %s failed: %s",
                provider_key,
                attempt + 1,
                error,
            )
            if attempt < len(REFRESH_RETRY_DELAYS):
                time.sleep(REFRESH_RETRY_DELAYS[attempt])
        except ProviderError as exc:
            return _error_response(_error_text(exc))
    return _error_response(error)


def charge(provider_key: str, ctx: ChargeContext) -> ChargeOutcome:
    adapter = get_adapter(provider_key)
    try:
        return run_authenticated(adapter, lambda: adapter.charge(ctx))
    except PermanentError as exc:
        return ChargeOutcome(result=ChargeResult.FAILED, message=_error_text(exc))
    except Exception as exc:
        log.warning("provider %s charge unverified: %s", provider_key, _error_text(exc))
        return ChargeOutcome(result=ChargeResult.UNVERIFIED, message=_error_text(exc))


def check_charge(provider_key: str, ctx: ChargeContext, provider_ref: str) -> ChargeOutcome:
    adapter = get_adapter(provider_key)
    try:
        return run_authenticated(adapter, lambda: adapter.check_charge(ctx, provider_ref))
    except Exception as exc:
        log.warning("provider %s check_charge failed: %s", provider_key, _error_text(exc))
        return ChargeOutcome(result=ChargeResult.UNVERIFIED, message=_error_text(exc))


def keepalive(provider_key: str) -> None:
    adapter = get_adapter(provider_key)
    run_authenticated(adapter, adapter.ping)
