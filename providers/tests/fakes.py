"""Configurable fake adapters for tests of code that charges through providers."""
from __future__ import annotations

from decimal import Decimal

from providers.contracts import (
    BaseAdapter,
    Capability,
    ChargeContext,
    ChargeOutcome,
    ChargeResult,
)


class FakeChargeAdapter(BaseAdapter):
    """Charge adapter whose behaviour each test sets via :meth:`configured`.

    ``raise_before_send`` is raised before the (pretend) request goes out,
    ``raise_after_send`` after it; ``sent`` counts requests that went out.
    """

    key = "fake"
    capabilities = frozenset({Capability.CHARGE, Capability.CHECK_CHARGE})
    needs_login = False
    check_without_ref = True

    result = ChargeResult.SUCCESS
    provider_ref = "FAKE-REF-1"
    plan_name = "Fake plan"
    provider_cost: Decimal | None = None
    message = ""
    raise_before_send: Exception | None = None
    raise_after_send: Exception | None = None
    check_result = ChargeResult.SUCCESS
    check_error: Exception | None = None
    login_error: Exception | None = None

    charges: list = []
    checks: list = []
    sent = 0
    logins = 0

    @classmethod
    def configured(cls, **options) -> type[FakeChargeAdapter]:
        """A fresh subclass with ``options`` as class attributes and empty call logs."""
        attrs = {"charges": [], "checks": [], "sent": 0, "logins": 0}
        attrs.update(options)
        return type("FakeChargeAdapter", (cls,), attrs)

    def _outcome(self, result: ChargeResult) -> ChargeOutcome:
        return ChargeOutcome(
            result=result,
            provider_ref=self.provider_ref,
            plan_name=self.plan_name,
            provider_cost=self.provider_cost,
            message=self.message,
        )

    def charge(self, ctx: ChargeContext) -> ChargeOutcome:
        type(self).charges.append(ctx)
        if self.raise_before_send is not None:
            raise self.raise_before_send
        type(self).sent += 1
        if self.raise_after_send is not None:
            raise self.raise_after_send
        return self._outcome(self.result)

    def check_charge(self, ctx: ChargeContext, provider_ref: str) -> ChargeOutcome:
        type(self).checks.append((ctx, provider_ref))
        if self.check_error is not None:
            raise self.check_error
        return self._outcome(self.check_result)

    def login(self) -> None:
        type(self).logins += 1
        if self.login_error is not None:
            raise self.login_error
