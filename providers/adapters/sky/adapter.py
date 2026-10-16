from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from typing import Any, Iterator

import requests

from providers.accounts import get_credentials
from providers.adapters.sky.client import (
    PROVIDER_KEY,
    SkySalesClient,
    SkySalesError,
    SkySalesSessionError,
)
from providers.adapters.sky.number_info import matches_phone, parse_number_info
from providers.adapters.sky.reconcile import SkyReconcileResult, reconcile_sky_report
from providers.adapters.sky.topup import (
    build_task,
    costs_match,
    find_period,
    quoted_cost,
    task_args,
    task_settled,
)
from providers.contracts import (
    AuthRequired,
    BaseAdapter,
    Capability,
    ChargeContext,
    ChargeOutcome,
    ChargeResult,
    NumberInfo,
    PermanentError,
    RawResponse,
    TransientError,
)
from providers.sanitize import scrub

_SKY_SYSTEM_ERROR_REASONS = frozenset(
    {
        "proxy_error",
        "login_error",
        "session_error",
        "otp_required",
        "config_error",
        "api_error",
        "connection_error",
    }
)


@contextmanager
def _sky_errors() -> Iterator[None]:
    """Translate Sky client failures into provider-gateway errors."""
    try:
        yield
    except SkySalesSessionError as exc:
        raise AuthRequired("session_expired") from exc
    except requests.Timeout as exc:
        raise TransientError("timeout") from exc
    except requests.RequestException as exc:
        raise TransientError("connection_error") from exc
    except SkySalesError as exc:
        raise PermanentError(scrub(str(exc))) from exc


class SkyAdapter(BaseAdapter):
    """Sky Sales Portal refresh via HTTP (sales-ps.sky5g.ps)."""

    key = PROVIDER_KEY
    capabilities = frozenset(
        {
            Capability.REFRESH,
            Capability.RECONCILE,
            Capability.NUMBER_INFO,
            Capability.CHARGE,
            Capability.CHECK_CHARGE,
        }
    )
    timeout: int = 120
    needs_login = True
    reconcile_template = "companies/sky_reconcile.html"

    def _stored_client(self) -> SkySalesClient:
        """Client for the session in Redis; ``AuthRequired`` if it can't be used."""
        client = SkySalesClient.from_store(get_credentials(PROVIDER_KEY))
        if client is None:
            raise AuthRequired("session not ready")
        try:
            usable = client.is_session_usable()
        except SkySalesError as exc:
            # Egress-IP detection through the account proxy failed.
            raise TransientError("proxy_error") from exc
        if not usable:
            raise AuthRequired("session not ready")
        return client

    def refresh(self, phone: str) -> RawResponse:
        phone = phone.strip()
        client = self._stored_client()
        with _sky_errors():
            result = client.refresh_sim(phone)
        reason = str(result.get("reason") or "")

        if reason in _SKY_SYSTEM_ERROR_REASONS:
            if result.get("error"):
                result["error"] = scrub(str(result["error"]))
            return RawResponse(
                text=json.dumps(result, ensure_ascii=False),
                json=result,
                status_code=0,
                error=result.get("error") or result.get("message"),
            )

        status_code = 200 if result.get("success") else 422
        return RawResponse(
            text=json.dumps(result, ensure_ascii=False),
            json=result,
            status_code=status_code,
        )

    def number_info(self, phone: str) -> NumberInfo:
        phone = phone.strip()
        client = self._stored_client()
        with _sky_errors():
            row = client.search_subscriber(phone)
        # ``search_subscriber`` falls back to the first row when nothing matches
        # exactly; a number check must not report another line's data.
        if not row or not matches_phone(row, phone):
            raise PermanentError("not_found")
        return parse_number_info(row)

    def _rows(self, data: dict) -> list[dict]:
        rows = data.get("data") or []
        return rows if isinstance(rows, list) else []

    def _call(self, client: SkySalesClient, name: str, args: list) -> dict:
        with _sky_errors():
            data = client.generic_api(name, args)
        if data.get("result") != "SUCCESS":
            raise PermanentError("provider_error")
        return data

    def charge(self, ctx: ChargeContext) -> ChargeOutcome:
        """Top up ``ctx.phone`` for ``ctx.duration_days`` once Sky's price matches."""
        phone = ctx.phone.strip()
        client = self._stored_client()
        with _sky_errors():
            row = client.search_subscriber(phone)
        if not row or not matches_phone(row, phone):
            raise PermanentError("not_found")
        if not ctx.duration_days:
            raise PermanentError("period_unavailable")

        low = {str(key).lower(): value for key, value in row.items()}
        plan_id = str(low.get("rate_plan_id") or "")
        periods = self._rows(
            self._call(client, "GetRatePlanPeriods", [plan_id, "AR", "extend"])
        )
        period = find_period(periods, ctx.duration_days)
        if period is None:
            raise PermanentError("period_unavailable")
        period_id = str({str(k).lower(): v for k, v in period.items()}.get("id") or "")

        cost_rows = self._rows(
            self._call(
                client,
                "GetSubscriberCosts",
                [
                    phone,
                    str(low.get("sub_customer_id") or ""),
                    plan_id,
                    period_id,
                    "sim-top-up",
                    0,
                ],
            )
        )
        if not cost_rows:
            raise PermanentError("provider_error")
        cost = {str(key).lower(): value for key, value in cost_rows[0].items()}
        quoted = quoted_cost(cost)
        if not costs_match(quoted, ctx.expected_cost):
            raise PermanentError("price_mismatch")

        self._call(client, "ValidateTaskDataBeforeCreate", [phone, ""])
        status_rows = self._rows(self._call(client, "GetSubscriberStatus", [str(low.get("id") or "")]))
        current = str(
            {str(k).lower(): v for k, v in (status_rows[0] if status_rows else {}).items()}.get(
                "status_id"
            )
            or ""
        )
        if current != str(low.get("status_id") or ""):
            raise PermanentError("not_eligible")

        money = {
            "order_cost": f"{float(cost.get('order_cost') or 0):.2f}",
            "order_refund": f"{float(cost.get('order_refund') or 0):.2f}",
            "order_total_cost": f"{float(cost.get('order_total_cost') or 0):.2f}",
        }
        with _sky_errors():
            global_customer = client.load_global_sub_customer_id() or str(
                low.get("sub_customer_id") or ""
            )
        task = build_task(
            row, period, money, phone=phone, global_customer_id=global_customer
        )
        # From here a task may exist at Sky even if the response is unreadable.
        try:
            with _sky_errors():
                created = client.generic_api(
                    "CreateNewTaskWithBalanceCheck",
                    task_args(
                        task,
                        user_id=client.session.eot_user_id,
                        sim_id=str(low.get("id") or ""),
                    ),
                )
        except Exception as exc:
            # The task may already exist; a login retry would charge the line again.
            raise TransientError("provider_error") from exc
        created_rows = self._rows(created)
        task_id = ""
        if created_rows:
            created_low = {str(k).lower(): v for k, v in created_rows[0].items()}
            task_id = str(created_low.get("id") or "").strip()
        if created.get("result") != "SUCCESS" or not task_id.isdigit():
            raise TransientError("provider_error")
        return ChargeOutcome(
            result=ChargeResult.SUCCESS,
            provider_ref=task_id,
            plan_name=str(low.get("rate_plan_name") or ""),
            provider_cost=quoted,
        )

    def check_charge(self, ctx: ChargeContext, provider_ref: str) -> ChargeOutcome:
        """Read the Sky task. Executed is success; cancelled or rejected is failed."""
        client = self._stored_client()
        with _sky_errors():
            data = client.generic_api("GetTaskDetails", [provider_ref, "AR"])
        rows = self._rows(data)
        state = task_settled(rows[0] if rows else None)
        if state == "success":
            return ChargeOutcome(result=ChargeResult.SUCCESS, provider_ref=provider_ref)
        if state == "failed":
            return ChargeOutcome(
                result=ChargeResult.FAILED, provider_ref=provider_ref, message="not_eligible"
            )
        return ChargeOutcome(result=ChargeResult.UNVERIFIED, provider_ref=provider_ref)

    def fetch_balance_rows(self, date_from: date, date_to: date) -> list[dict[str, Any]]:
        """Sky template-22 balance rows for the logged-in account."""
        client = self._stored_client()
        with _sky_errors():
            return client.fetch_balance_report(date_from, date_to)

    def reconcile(
        self,
        company,
        period_from: date | None,
        period_to: date | None,
        *,
        min_amount_diff: Decimal | None = None,
    ) -> SkyReconcileResult:
        """Live Sky balance rows for the period, matched against our sales."""
        rows = self.fetch_balance_rows(period_from, period_to)
        return reconcile_sky_report(
            company,
            rows,
            period_from=period_from,
            period_to=period_to,
            min_amount_diff=min_amount_diff,
        )

    def login(self) -> None:
        creds = get_credentials(PROVIDER_KEY)
        client = SkySalesClient(credentials=creds)
        client.force_new_session(creds.username, creds.password)

    def ping(self) -> None:
        client = self._stored_client()
        with _sky_errors():
            client._probe_session()
