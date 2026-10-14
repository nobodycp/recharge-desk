"""Synchronous provider API used by the rest of the project."""
from __future__ import annotations

import logging
import time

from providers.contracts import (
    RECONCILE_NOT_SUPPORTED,
    AuthRequired,
    Capability,
    ChargeContext,
    ChargeOutcome,
    ChargeResult,
    PermanentError,
    ProviderError,
    RawResponse,
    TransientError,
)
from providers.registry import ADAPTERS, get_adapter
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


def _not_sent_code(exc: Exception) -> str:
    if isinstance(exc, AuthRequired):
        return "auth_required"
    if isinstance(exc, TransientError):
        return _error_text(exc)
    return exc.__class__.__name__


def charge(
    provider_key: str, ctx: ChargeContext, budget: float = SYNC_BUDGET_SECONDS
) -> ChargeOutcome:
    """Charge once; never retried.

    ``FAILED`` when the provider rejected the charge or no request ever reached
    it (login busy/failed, ``AuthRequired`` that persists after a login: message
    ``not_sent:<code>``); ``UNVERIFIED`` when a request may have been sent.
    """
    adapter = get_adapter(provider_key)
    deadline = time.monotonic() + budget
    may_have_sent = False

    def attempt() -> ChargeOutcome:
        nonlocal may_have_sent
        try:
            return adapter.charge(ctx)
        except AuthRequired:
            raise
        except BaseException:
            may_have_sent = True
            raise

    try:
        return run_authenticated(adapter, attempt, lock_wait=_lock_wait(deadline))
    except PermanentError as exc:
        return ChargeOutcome(result=ChargeResult.FAILED, message=_error_text(exc))
    except Exception as exc:
        if not may_have_sent:
            message = f"not_sent:{_not_sent_code(exc)}"
            log.warning("provider %s charge not sent: %s", provider_key, message)
            return ChargeOutcome(result=ChargeResult.FAILED, message=message)
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


def company_supports(
    company, capability: Capability, provider: str | None = None
) -> bool:
    """True when the company's explicit ``phone_refresh_provider`` has ``capability``.

    ``provider`` additionally requires that exact provider key. Never inspects
    the company name: routing comes from the field only.
    """
    key = (company.phone_refresh_provider or "").strip().lower()
    if provider is not None and key != provider:
        return False
    adapter_cls = ADAPTERS.get(key)
    return adapter_cls is not None and capability in adapter_cls.capabilities


def reconcile(
    company,
    period_from,
    period_to,
    *,
    budget: float = SYNC_BUDGET_SECONDS,
    **opts,
) -> tuple[object, str]:
    """Run the company's provider reconciliation; returns ``(result, template)``.

    Runs synchronously in the web request (admin-only), so lock waits stay within
    ``budget`` seconds; a Sky ``AuthRequired`` triggers one login and a retry.
    Raises ``PermanentError(RECONCILE_NOT_SUPPORTED)`` when the company has no
    provider or its provider cannot reconcile.
    """
    if not company_supports(company, Capability.RECONCILE):
        raise PermanentError(RECONCILE_NOT_SUPPORTED)
    adapter = get_adapter((company.phone_refresh_provider or "").strip().lower())
    deadline = time.monotonic() + budget

    def call():
        return adapter.reconcile(company, period_from, period_to, **opts)

    if adapter.needs_login:
        result = run_authenticated(adapter, call, lock_wait=_lock_wait(deadline))
    else:
        result = call()
    return result, adapter.reconcile_template


def keepalive(provider_key: str) -> None:
    adapter = get_adapter(provider_key)
    run_authenticated(adapter, adapter.ping)
