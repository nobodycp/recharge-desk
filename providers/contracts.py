"""Shared provider-gateway contract: result types, errors and the adapter base class."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
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


# ``PermanentError`` message when a company's provider cannot reconcile.
RECONCILE_NOT_SUPPORTED = "reconcile_not_supported"


class Capability(str, Enum):
    REFRESH = "refresh"
    CHARGE = "charge"
    CHECK_CHARGE = "check_charge"
    RECONCILE = "reconcile"


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


@dataclass
class ChargeOutcome:
    result: ChargeResult
    provider_ref: str = ""
    plan_name: str = ""
    provider_cost: Decimal | None = None
    message: str = ""
    raw_excerpt: str = ""


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
    reconcile_template: str = ""

    def refresh(self, phone: str) -> RawResponse:
        raise NotImplementedError

    def charge(self, ctx: ChargeContext) -> ChargeOutcome:
        raise NotImplementedError

    def check_charge(self, ctx: ChargeContext, provider_ref: str) -> ChargeOutcome:
        raise NotImplementedError

    def reconcile(self, company, period_from, period_to, **opts):
        raise NotImplementedError

    def login(self) -> None:
        raise NotImplementedError

    def ping(self) -> None:
        raise NotImplementedError
