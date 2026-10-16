"""Shared provider-gateway contract: result types, errors and the adapter base class."""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any


@dataclass
class RawResponse:
    """Normalized envelope returned by every provider's ``refresh`` method.

    The pattern matcher consumes only these four fields, which keeps the
    upstream-specific transport details fully encapsulated in each
    provider subclass.
    """

    text: str
    json: Any | None
    status_code: int
    error: str | None = None
    html_form_detected: bool = False
    # How many provider attempts ``providers.service.refresh`` made (diagnostics).
    attempts: int = 1


# ``PermanentError`` message when a company's provider cannot reconcile.
RECONCILE_NOT_SUPPORTED = "reconcile_not_supported"


class Capability(str, Enum):
    REFRESH = "refresh"
    CHARGE = "charge"
    CHECK_CHARGE = "check_charge"
    RECONCILE = "reconcile"
    NUMBER_INFO = "number_info"


class ChargeResult(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    UNVERIFIED = "unverified"


@dataclass
class ChargeContext:
    sale_id: int
    phone: str
    expected_cost: Decimal
    product_name: str
    is_esim: bool
    idempotency_key: str
    requested_at: datetime | None = None
    # Charged length in days. Sky matches this to a period (3 days, 1 month = 30, …).
    duration_days: int | None = None


@dataclass
class ChargeOutcome:
    result: ChargeResult
    provider_ref: str = ""
    plan_name: str = ""
    provider_cost: Decimal | None = None
    message: str = ""
    raw_excerpt: str = ""
    # False when no charge request reached the provider. The sale is then
    # dropped instead of left for management.
    reached_provider: bool = True
    # The provider refused before accepting a charge. The employee sees the
    # reason; management does not.
    employee_only: bool = False


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _datetime_or_none(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


@dataclass
class NumberInfo:
    """Live data of one line as reported by the provider (``number_info``)."""

    network: str = ""
    is_esim: bool = False
    status: str = ""
    plan_name: str = ""
    # Provider-side price: stored on the job result, never sent to the browser.
    plan_price: Decimal | None = None
    ends_at: datetime | None = None
    data_used_gb: Decimal | None = None
    data_total_gb: Decimal | None = None
    minutes_used: int | None = None
    minutes_total: int | None = None
    sms_used: int | None = None
    sms_total: int | None = None
    utilization_pct: Decimal | None = None
    iccid: str = ""

    _DECIMALS = (
        "plan_price",
        "data_used_gb",
        "data_total_gb",
        "utilization_pct",
    )
    _INTS = ("minutes_used", "minutes_total", "sms_used", "sms_total")
    _STRINGS = ("network", "status", "plan_name", "iccid")

    def to_dict(self) -> dict:
        """JSON-safe form: ``Decimal`` as ``str``, ``datetime`` as ISO-8601 with offset."""
        data: dict[str, Any] = {}
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, Decimal):
                value = str(value)
            elif isinstance(value, datetime):
                value = value.isoformat()
            data[field.name] = value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> NumberInfo:
        """Inverse of ``to_dict``; missing, unknown or malformed keys are tolerated."""
        data = data or {}
        kwargs: dict[str, Any] = {}
        for name in cls._STRINGS:
            value = data.get(name)
            kwargs[name] = "" if value is None else str(value)
        for name in cls._DECIMALS:
            kwargs[name] = _decimal_or_none(data.get(name))
        for name in cls._INTS:
            kwargs[name] = _int_or_none(data.get(name))
        kwargs["is_esim"] = bool(data.get("is_esim", False))
        kwargs["ends_at"] = _datetime_or_none(data.get("ends_at"))
        return cls(**kwargs)


class ProviderError(Exception):
    """Base class for provider-gateway errors."""


class AuthRequired(ProviderError):
    """The provider session is missing or expired and a login is needed."""


class TransientError(ProviderError):
    """A temporary failure; the operation may be retried."""


class PermanentError(ProviderError):
    """A failure that retrying will not fix."""


class BaseAdapter:
    """Base class for provider adapters.

    Subclasses set ``key`` (the registry key) and ``capabilities`` and
    override only the operations they support.
    """

    key: str = ""
    capabilities: frozenset = frozenset()
    timeout: int = 20
    needs_login: bool = False
    # True if ``check_charge`` can find a charge by phone/idempotency key alone.
    check_without_ref: bool = False
    reconcile_template: str = ""

    def refresh(self, phone: str) -> RawResponse:
        raise NotImplementedError

    def number_info(self, phone: str) -> NumberInfo:
        """Live data of ``phone`` (read-only).

        Raise ``PermanentError("not_found")`` when the provider has no such line.
        """
        raise NotImplementedError

    def charge(self, ctx: ChargeContext) -> ChargeOutcome:
        """Charge ``ctx.phone`` once (the provider finds the line by number).

        Raise ``AuthRequired`` only if no charge request reached the provider
        (the sale is then reversed). Once a request may have been sent, any
        failure must be ``TransientError`` (the charge stays unverified until
        ``check_charge`` settles it); ``PermanentError`` means the provider
        definitely rejected it.
        """
        raise NotImplementedError

    def check_charge(self, ctx: ChargeContext, provider_ref: str) -> ChargeOutcome:
        """Report what happened to an earlier charge (never charges).

        ``FAILED`` only when the provider definitively shows no charge for
        this phone / idempotency key since ``ctx.requested_at`` (the sale is
        then reversed); ``SUCCESS`` when it shows the charge; anything
        uncertain is ``UNVERIFIED``. ``provider_ref`` may be empty only for
        adapters that set ``check_without_ref``.
        """
        raise NotImplementedError

    def reconcile(self, company, period_from, period_to, **opts):
        raise NotImplementedError

    def login(self) -> None:
        raise NotImplementedError

    def ping(self) -> None:
        raise NotImplementedError
