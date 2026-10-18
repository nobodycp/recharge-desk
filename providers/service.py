"""Synchronous provider API used by the rest of the project."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from time import monotonic as _clock

from providers.contracts import (
    RECONCILE_NOT_SUPPORTED,
    AuthRequired,
    Capability,
    ChargeContext,
    ChargeOutcome,
    ChargeResult,
    NumberInfo,
    PermanentError,
    ProviderError,
    RawResponse,
    TransientError,
)
from providers import accounts, operations
from providers.models import ProviderConnection
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
# The provider answered and refused a charge, without a task being created.
# The employee sees the reason. The sale is not left for management.
PROVIDER_ANSWER_CODES = frozenset(
    {
        "price_mismatch",
        "period_unavailable",
        "not_found",
        "not_eligible",
        "provider_error",
    }
)


def _error_text(exc: Exception) -> str:
    return scrub(str(exc)) or exc.__class__.__name__


def _error_response(error: str, attempts: int = 1) -> RawResponse:
    return RawResponse(text="", json=None, status_code=0, error=error, attempts=attempts)


def _lock_wait(deadline: float, budget: float) -> float:
    """Login lock wait left before ``deadline``.

    Sync callers (budget <= ``SYNC_BUDGET_SECONDS``) keep the web cap; workers
    may wait out another worker's login for the rest of their budget.
    """
    if budget > SYNC_BUDGET_SECONDS:
        cap = sessions.LOGIN_LOCK_TIMEOUT_SECONDS
    else:
        cap = sessions.LOGIN_LOCK_WAIT_SECONDS
    return max(0.0, min(cap, deadline - time.monotonic()))


def refresh(
    provider_key: str, phone: str, budget: float = SYNC_BUDGET_SECONDS
) -> RawResponse:
    """Refresh ``phone``; transient failures are retried, errors become a RawResponse.

    Retries and lock waits stay within ``budget`` seconds; a budget above
    ``SYNC_BUDGET_SECONDS`` (worker) may wait on another login past the web
    lock cap. Raises ``KeyError``
    for an unknown ``provider_key``.
    """
    adapter = get_adapter(provider_key)
    deadline = time.monotonic() + budget
    error = "sync_budget_exceeded"
    made = 0  # attempts actually started (recorded on every returned response)
    for attempt in range(REFRESH_ATTEMPTS):
        if time.monotonic() >= deadline:
            break
        made += 1
        try:
            raw = run_authenticated(
                adapter,
                lambda: adapter.refresh(phone),
                lock_wait=_lock_wait(deadline, budget),
            )
            return replace(raw, attempts=made)
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
            return _error_response(_error_text(exc), made)
    return _error_response(error, made)


@dataclass(frozen=True)
class NumberInfoResult:
    """Outcome of a number check: the live ``info`` or the classified failure."""

    info: NumberInfo | None
    classified: operations.Classified

    @property
    def ok(self) -> bool:
        return self.info is not None


def _number_info_attempts(
    adapter, provider_key: str, phone: str, budget: float
) -> tuple[NumberInfo | None, operations.Classified, int, str]:
    """Run the attempt loop; returns ``(info, classified, attempts_started, detail)``."""
    deadline = time.monotonic() + budget
    made = 0
    classified = operations.classify_code("number_info", "sync_budget_exceeded")
    detail = "sync_budget_exceeded"
    for attempt in range(REFRESH_ATTEMPTS):
        if time.monotonic() >= deadline:
            break
        made += 1
        try:
            info = run_authenticated(
                adapter,
                lambda: adapter.number_info(phone),
                lock_wait=_lock_wait(deadline, budget),
            )
        except TransientError as exc:
            detail = _error_text(exc)
            classified = operations.classify_exception("number_info", exc)
            log.warning(
                "provider %s number_info attempt %s failed: %s",
                provider_key,
                attempt + 1,
                detail,
            )
            if str(exc) in NO_RETRY_TRANSIENT_CODES or attempt >= len(REFRESH_RETRY_DELAYS):
                break
            delay = REFRESH_RETRY_DELAYS[attempt]
            if time.monotonic() + delay >= deadline:
                break
            time.sleep(delay)
        except ProviderError as exc:
            detail = _error_text(exc)
            if isinstance(exc, PermanentError) and str(exc) == "not_found":
                classified = operations.not_found("number_info")
            else:
                classified = operations.classify_exception("number_info", exc)
            return None, classified, made, detail
        except Exception as exc:  # noqa: BLE001 — a check must never raise
            log.warning(
                "provider %s number_info crashed: %s", provider_key, exc.__class__.__name__
            )
            detail = f"{exc.__class__.__name__}: {_error_text(exc)}"
            return None, operations.classify_exception("number_info", exc), made, detail
        else:
            return info, operations.success("number_info"), made, ""
    return None, classified, made, detail


def number_info(
    provider_key: str,
    phone: str,
    budget: float = SYNC_BUDGET_SECONDS,
    *,
    source: str = "employee",
    user_id: int | None = None,
    sale_id: int | None = None,
    job_id=None,
) -> NumberInfoResult:
    """Read the live data of ``phone`` from the provider; one operation row is recorded.

    Transient failures are retried like ``refresh`` (within ``budget``); a missing
    line is the named ``not_found`` failure. Never raises, except ``KeyError``
    for an unknown ``provider_key``.
    """
    adapter = get_adapter(provider_key)
    started = _clock()
    info, classified, made, detail = _number_info_attempts(adapter, provider_key, phone, budget)
    try:
        operations.record(
            provider_key,
            "number_info",
            classified,
            phone=phone,
            source=source,
            user_id=user_id,
            sale_id=sale_id,
            job_id=job_id,
            attempts=made,
            duration_ms=_elapsed_ms(started),
            detail=detail,
        )
    except Exception as exc:  # noqa: BLE001 — recording must never break a check
        log.warning("provider %s number_info not recorded: %s", provider_key, _error_text(exc))
    return NumberInfoResult(info=info, classified=classified)


def _not_sent_code(exc: Exception) -> str:
    if isinstance(exc, AuthRequired):
        return "auth_required"
    if isinstance(exc, TransientError):
        return _error_text(exc)
    return exc.__class__.__name__


def _elapsed_ms(started: float) -> int:
    return max(0, int((_clock() - started) * 1000))


def _record_charge_operation(
    provider_key: str,
    operation: str,
    ctx: ChargeContext,
    outcome: ChargeOutcome,
    started: float,
    *,
    source: str,
    user_id: int | None,
    job_id,
) -> ChargeOutcome:
    """Log ``outcome`` as one operation row; return it with the Arabic message.

    The provider's own message (raw code / text) goes to the row's ``detail``.
    Recording can never change what the caller gets besides the message.
    """
    classified = operations.classify_charge(operation, outcome)
    detail = " | ".join(part for part in (outcome.message, outcome.raw_excerpt) if part)
    try:
        operations.record(
            provider_key,
            operation,
            classified,
            phone=ctx.phone,
            source=source,
            user_id=user_id,
            sale_id=ctx.sale_id,
            job_id=job_id,
            duration_ms=_elapsed_ms(started),
            detail=detail,
        )
    except Exception as exc:  # noqa: BLE001 — recording must never break a charge
        log.warning("provider %s %s not recorded: %s", provider_key, operation, _error_text(exc))
    return replace(outcome, message=classified.message)


def charge(
    provider_key: str,
    ctx: ChargeContext,
    budget: float = SYNC_BUDGET_SECONDS,
    *,
    source: str = "employee",
    user_id: int | None = None,
    job_id=None,
) -> ChargeOutcome:
    """Charge once; never retried; one operation row is recorded.

    ``FAILED`` when the provider rejected the charge or no request ever reached
    it (login busy/failed, ``AuthRequired`` that persists after a login);
    ``UNVERIFIED`` when a request may have been sent. The returned ``message``
    is the named Arabic message (the provider's own text is kept in the row's
    ``detail``). Raises ``KeyError`` for an unknown ``provider_key``.
    """
    adapter = get_adapter(provider_key)
    started = _clock()
    outcome = _charge_once(adapter, provider_key, ctx, budget)
    return _record_charge_operation(
        provider_key,
        "charge",
        ctx,
        outcome,
        started,
        source=source,
        user_id=user_id,
        job_id=job_id,
    )


def _charge_once(
    adapter, provider_key: str, ctx: ChargeContext, budget: float
) -> ChargeOutcome:
    deadline = time.monotonic() + budget
    may_have_sent = False

    def attempt() -> ChargeOutcome:
        nonlocal may_have_sent
        try:
            return adapter.charge(ctx)
        except AuthRequired:
            raise
        except TransientError:
            # Adapters raise this only once the charge request may have left.
            may_have_sent = True
            raise

    try:
        return run_authenticated(adapter, attempt, lock_wait=_lock_wait(deadline, budget))
    except PermanentError as exc:
        code = _error_text(exc)
        answered = (not may_have_sent) and code in PROVIDER_ANSWER_CODES
        return ChargeOutcome(
            result=ChargeResult.FAILED,
            message=code,
            reached_provider=may_have_sent,
            employee_only=answered,
        )
    except Exception as exc:
        if not may_have_sent:
            message = f"not_sent:{_not_sent_code(exc)}"
            log.warning("provider %s charge not sent: %s", provider_key, message)
            return ChargeOutcome(
                result=ChargeResult.FAILED, message=message, reached_provider=False
            )
        log.warning("provider %s charge unverified: %s", provider_key, _error_text(exc))
        return ChargeOutcome(result=ChargeResult.UNVERIFIED, message=_error_text(exc))


def check_charge(
    provider_key: str,
    ctx: ChargeContext,
    provider_ref: str,
    budget: float = SYNC_BUDGET_SECONDS,
    *,
    source: str = "system",
    job_id=None,
) -> ChargeOutcome:
    """Ask the provider what happened to an earlier charge; never charges.

    Records one operation row and returns the outcome with the named Arabic
    message. Without a ref (and an adapter that needs one) no provider is
    called, so nothing is recorded.
    """
    adapter = get_adapter(provider_key)
    if not provider_ref and not adapter.check_without_ref:
        return ChargeOutcome(result=ChargeResult.UNVERIFIED, message="no_provider_ref")
    started = _clock()
    deadline = time.monotonic() + budget
    try:
        outcome = run_authenticated(
            adapter,
            lambda: adapter.check_charge(ctx, provider_ref),
            lock_wait=_lock_wait(deadline, budget),
        )
    except Exception as exc:
        log.warning("provider %s check_charge failed: %s", provider_key, _error_text(exc))
        outcome = ChargeOutcome(result=ChargeResult.UNVERIFIED, message=_error_text(exc))
    return _record_charge_operation(
        provider_key,
        "check_charge",
        ctx,
        outcome,
        started,
        source=source,
        user_id=None,
        job_id=job_id,
    )


@dataclass(frozen=True)
class ServiceState:
    """Whether a provider service can run for a company.

    ``code`` is ``active``, ``off``, ``unsupported`` or ``not_configured``;
    ``reason`` is the Arabic explanation (empty when active).
    """

    code: str
    reason: str = ""


_NO_SYSTEM = "لم يتم تحديد نظام الشركة"
_NOT_AVAILABLE = "غير متاح لهذا النظام بعد"
_NOT_CONFIGURED = "بيانات الحساب ناقصة أو غير مقروءة"
_OFF = "موقوفة"


def _switch_field(capability: Capability) -> str | None:
    if capability is Capability.CHECK_CHARGE:
        capability = Capability.CHARGE
    return ProviderConnection.SERVICE_FIELDS.get(capability)


def _switches_on(connection: ProviderConnection | None, capability: Capability) -> bool:
    """Master and service switch of ``connection`` (model defaults without a row)."""
    if connection is None:
        connection = ProviderConnection(provider_key="")
    if not connection.is_enabled:
        return False
    field = _switch_field(capability)
    return True if field is None else bool(getattr(connection, field))


def service_enabled(provider_key: str, capability: Capability) -> bool:
    """Master and per-service switches for ``provider_key``.

    Read-only (hot path): never creates a connection row; without one the model
    field defaults apply. ``CHECK_CHARGE`` follows ``charge_enabled``.
    """
    connection = ProviderConnection.objects.filter(provider_key=provider_key).first()
    return _switches_on(connection, capability)


def _capability_needs_login(adapter_cls, capability: Capability) -> bool:
    """True when this capability cannot run without stored provider credentials."""
    if not adapter_cls.needs_login:
        return False
    limited = adapter_cls.login_capabilities
    return True if limited is None else capability in limited


def service_state(
    company, capability: Capability, provider: str | None = None
) -> ServiceState:
    """Gate for ``capability`` on the company's explicit ``provider_key``.

    ``provider`` additionally requires that exact provider key. Never inspects
    the company name. A connection row alone is not an account: for adapters
    that log in, credentials must resolve via ``accounts.get_credentials``.
    """
    key = (company.provider_key or "").strip().lower()
    adapter_cls = ADAPTERS.get(key)
    if adapter_cls is None:
        return ServiceState("unsupported", _NO_SYSTEM)
    if provider is not None and key != provider:
        return ServiceState("unsupported", _NOT_AVAILABLE)
    if capability not in adapter_cls.capabilities:
        return ServiceState("unsupported", _NOT_AVAILABLE)
    connection = ProviderConnection.objects.filter(provider_key=key).first()
    if connection is not None and not connection.is_enabled:
        return ServiceState("off", _OFF)
    if not _switches_on(connection, capability):
        return ServiceState("off", _OFF)
    if _capability_needs_login(adapter_cls, capability):
        try:
            accounts.get_credentials(key)
        except PermanentError as exc:
            if str(exc) == accounts.PROVIDER_DISABLED:
                return ServiceState("off", _OFF)
            if str(exc) == "config_error":
                return ServiceState("not_configured", _NOT_CONFIGURED)
            raise
    return ServiceState("active")


def _record_reconcile(provider_key: str, classified, started: float, *, source, user_id, detail=""):
    try:
        operations.record(
            provider_key,
            "reconcile",
            classified,
            source=source,
            user_id=user_id,
            duration_ms=_elapsed_ms(started),
            detail=detail,
        )
    except Exception as exc:  # noqa: BLE001 — recording must never break reconcile
        log.warning("provider %s reconcile not recorded: %s", provider_key, _error_text(exc))


_GATE_CODES = {"off": "provider_disabled", "not_configured": "not_configured"}


def reconcile(
    company,
    period_from,
    period_to,
    *,
    budget: float = SYNC_BUDGET_SECONDS,
    source: str = "management",
    user_id: int | None = None,
    **opts,
) -> tuple[object, str]:
    """Run the company's provider reconciliation; returns ``(result, template)``.

    Runs synchronously in the web request (admin-only), so lock waits stay within
    ``budget`` seconds; a Sky ``AuthRequired`` triggers one login and a retry.
    Raises ``PermanentError(RECONCILE_NOT_SUPPORTED)`` unless the company's
    reconcile ``service_state`` is ``active`` (no provider, unsupported,
    switched off or not configured).

    One operation row is recorded for the run (success or the classified
    failure; exceptions still propagate unchanged). The gate only writes a row
    when the company has a provider whose reconcile is switched off or not
    configured; no provider / unsupported provider write nothing.
    """
    started = _clock()
    provider_key = (company.provider_key or "").strip().lower()
    state = service_state(company, Capability.RECONCILE)
    if state.code != "active":
        code = _GATE_CODES.get(state.code)
        if provider_key and code is not None:
            _record_reconcile(
                provider_key,
                operations.classify_code("reconcile", code),
                started,
                source=source,
                user_id=user_id,
                detail=state.code,
            )
        raise PermanentError(RECONCILE_NOT_SUPPORTED)
    try:
        adapter = get_adapter(provider_key)
        deadline = time.monotonic() + budget

        def call():
            return adapter.reconcile(company, period_from, period_to, **opts)

        if adapter.needs_login:
            result = run_authenticated(adapter, call, lock_wait=_lock_wait(deadline, budget))
        else:
            result = call()
    except Exception as exc:
        _record_reconcile(
            provider_key,
            operations.classify_exception("reconcile", exc),
            started,
            source=source,
            user_id=user_id,
            detail=f"{exc.__class__.__name__}: {_error_text(exc)}",
        )
        raise
    row_count = getattr(result, "row_count", None)
    _record_reconcile(
        provider_key,
        operations.success("reconcile"),
        started,
        source=source,
        user_id=user_id,
        detail=f"{row_count} rows" if row_count is not None else "",
    )
    return result, adapter.reconcile_template


def keepalive(provider_key: str) -> None:
    adapter = get_adapter(provider_key)
    run_authenticated(adapter, adapter.ping)
