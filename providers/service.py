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
from providers import sessions
from providers.sessions import run_authenticated

log = logging.getLogger(__name__)

REFRESH_ATTEMPTS = 3
REFRESH_RETRY_DELAYS = (1, 2)
# Total time a sync caller (web request, gunicorn timeout 60 s) may spend
# waiting/retrying; no new attempt, sleep or lock wait starts past it.
SYNC_BUDGET_SECONDS = 45
# Login-path failures: retrying would only queue on the lock again.
NO_RETRY_TRANSIENT_CODES = frozenset({"login_busy", "login_failed"})


def _error_text(exc: Exception) -> str:
    return scrub(str(exc)) or exc.__class__.__name__


def _error_response(error: str) -> RawResponse:
    return RawResponse(text="", json=None, status_code=0, error=error)


def _lock_wait(deadline: float) -> float:
    return max(0.0, min(sessions.LOGIN_LOCK_WAIT_SECONDS, deadline - time.monotonic()))


def refresh(
    provider_key: str, phone: str, budget: float = SYNC_BUDGET_SECONDS
) -> RawResponse:
    """Refresh ``phone``; transient failures are retried, errors become a RawResponse.

    Retries and lock waits stay within ``budget`` seconds. Raises ``KeyError``
    for an unknown ``provider_key``.
    """
    adapter = get_adapter(provider_key)
    deadline = time.monotonic() + budget
    error = "sync_budget_exceeded"
    for attempt in range(REFRESH_ATTEMPTS):
        if time.monotonic() >= deadline:
            break
        try:
            return run_authenticated(
                adapter, lambda: adapter.refresh(phone), lock_wait=_lock_wait(deadline)
            )
        except TransientError as exc:
            error = _error_text(exc)
            log.warning(
                "provider %s refresh attempt %s failed: %s",
                provider_key,
                attempt + 1,
                error,
            )
            if str(exc) in NO_RETRY_TRANSIENT_CODES or attempt >= len(REFRESH_RETRY_DELAYS):
                break
            delay = REFRESH_RETRY_DELAYS[attempt]
            if time.monotonic() + delay >= deadline:
                break
            time.sleep(delay)
        except ProviderError as exc:
            return _error_response(_error_text(exc))
    return _error_response(error)


def charge(
    provider_key: str, ctx: ChargeContext, budget: float = SYNC_BUDGET_SECONDS
) -> ChargeOutcome:
    adapter = get_adapter(provider_key)
    deadline = time.monotonic() + budget
    try:
        return run_authenticated(
            adapter, lambda: adapter.charge(ctx), lock_wait=_lock_wait(deadline)
        )
    except PermanentError as exc:
        return ChargeOutcome(result=ChargeResult.FAILED, message=_error_text(exc))
    except Exception as exc:
        log.warning("provider %s charge unverified: %s", provider_key, _error_text(exc))
        return ChargeOutcome(result=ChargeResult.UNVERIFIED, message=_error_text(exc))


def check_charge(
    provider_key: str,
    ctx: ChargeContext,
    provider_ref: str,
    budget: float = SYNC_BUDGET_SECONDS,
) -> ChargeOutcome:
    adapter = get_adapter(provider_key)
    deadline = time.monotonic() + budget
    try:
        return run_authenticated(
            adapter,
            lambda: adapter.check_charge(ctx, provider_ref),
            lock_wait=_lock_wait(deadline),
        )
    except Exception as exc:
        log.warning("provider %s check_charge failed: %s", provider_key, _error_text(exc))
        return ChargeOutcome(result=ChargeResult.UNVERIFIED, message=_error_text(exc))


def keepalive(provider_key: str) -> None:
    adapter = get_adapter(provider_key)
    run_authenticated(adapter, adapter.ping)
