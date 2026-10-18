from __future__ import annotations

from datetime import date
from decimal import Decimal

import requests

from providers.accounts import get_credentials
from providers.adapters.layan.client import (
    PROVIDER_KEY,
    LayanClient,
    LayanError,
    LayanSessionError,
)
from providers.adapters.layan import charge as layan_charge
from providers.adapters.layan.number_info import parse_number_info
from providers.adapters.layan.reconcile import (
    LayanReconcileResult,
    parse_pending_credits,
    reconcile_layan_report,
)
from providers.shared.phones import norm_phone
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


class LayanAdapter(BaseAdapter):
    key = "layan"
    capabilities = frozenset(
        {
            Capability.REFRESH,
            Capability.RECONCILE,
            Capability.NUMBER_INFO,
            Capability.CHARGE,
        }
    )
    # Number refresh stays on the public endpoint. Matching and number check sign in.
    needs_login = True
    login_capabilities = frozenset(
        {Capability.RECONCILE, Capability.NUMBER_INFO, Capability.CHARGE}
    )
    reconcile_template = "companies/layan_reconcile.html"

    URL = "https://api.layan-t.net/api/Subscribtions/CustomersRefreshNumber"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:149.0) Gecko/20100101 Firefox/149.0",
        "Accept": "*/*",
        "Content-Type": "application/json",
        "LANG": "ar",
        "Origin": "https://rn.layan-t.net",
        "Referer": "https://rn.layan-t.net/",
    }

    def refresh(self, phone: str) -> RawResponse:
        try:
            r = requests.post(
                self.URL,
                json={"number": phone},
                headers=self.HEADERS,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            return RawResponse(text="", json=None, status_code=0, error=str(exc))

        try:
            payload = r.json()
        except ValueError:
            payload = None
        return RawResponse(text=r.text, json=payload, status_code=r.status_code)

    def _stored_client(self) -> LayanClient:
        client = LayanClient.from_store(get_credentials(PROVIDER_KEY))
        if client is None or not client.is_session_usable():
            raise AuthRequired("session not ready")
        return client

    def _call(self, fn):
        try:
            return fn()
        except LayanSessionError as exc:
            raise AuthRequired("session_expired") from exc
        except requests.Timeout as exc:
            raise TransientError("timeout") from exc
        except requests.RequestException as exc:
            raise TransientError("connection_error") from exc
        except LayanError as exc:
            raise PermanentError(str(exc)) from exc

    def number_info(self, phone: str) -> NumberInfo:
        phone = phone.strip()
        client = self._stored_client()
        subscription = self._call(lambda: client.fetch_subscription(phone))
        row = _matching_subscription(subscription, phone)
        if row is None:
            raise PermanentError("not_found")
        usage = self._call(lambda: client.fetch_usage(phone))
        return parse_number_info(row, usage)

    def charge(self, ctx: ChargeContext) -> ChargeOutcome:
        """Extend or activate ``ctx.phone`` once Layan's quote matches our cost."""
        phone = ctx.phone.strip()
        client = self._stored_client()
        subscription = self._call(lambda: client.fetch_subscription(phone))
        row = _matching_subscription(subscription, phone)
        if row is None:
            raise PermanentError("not_found")

        usage = self._call(lambda: client.fetch_usage(phone)) or {}
        number = usage.get("number") if isinstance(usage.get("number"), dict) else {}
        status = str(number.get("status") or "")
        extend = layan_charge.is_active_status(status)

        duration: int | None
        sale_id: str | None
        if ctx.duration_days == 90:
            sales = self._call(lambda: client.get_sales_by_number(phone))
            promo = layan_charge.pick_three_month_sale(sales)
            if promo is None:
                raise PermanentError("period_unavailable")
            sale_id = str(promo.get("id") or "").strip() or None
            if not sale_id:
                raise PermanentError("period_unavailable")
            duration = None
        else:
            if not ctx.duration_days:
                raise PermanentError("period_unavailable")
            duration = int(ctx.duration_days)
            sale_id = None

        quote_body = self._call(
            lambda: client.get_cost_before_activate(
                phone, duration=duration, sale_id=sale_id
            )
        )
        quoted = layan_charge.parse_cost_quote(quote_body)
        if not layan_charge.money_match(quoted, ctx.expected_cost):
            raise PermanentError("price_mismatch")

        if extend:
            code, body = self._call(
                lambda: client.extend_line(
                    phone, duration=duration, sale_id=sale_id, user_paid=True
                )
            )
        else:
            end_date = str(row.get("endDate") or "").strip() or None
            code, body = self._call(
                lambda: client.activate_line(
                    phone,
                    duration=duration,
                    sale_id=sale_id,
                    end_date=end_date,
                    user_paid=True,
                )
            )
        if not layan_charge.is_charge_success(code, body):
            raise PermanentError("provider_error")

        package = row.get("package") if isinstance(row.get("package"), dict) else {}
        plan_name = str(package.get("name") or "").strip()
        return ChargeOutcome(
            result=ChargeResult.SUCCESS,
            provider_ref=str(body).strip() if body else "",
            plan_name=plan_name,
            provider_cost=quoted,
        )

    def fetch_accounting_rows(self, date_from: date, date_to: date) -> list[dict]:
        client = self._stored_client()
        return self._call(lambda: client.fetch_accounting(date_from, date_to))

    def reconcile(
        self,
        company,
        period_from: date | None,
        period_to: date | None,
        *,
        min_amount_diff: Decimal | None = None,
        pending_credits_text: str = "",
    ) -> LayanReconcileResult:
        """Live Layan accounting rows for the period, matched against our sales."""
        if period_from is None or period_to is None:
            raise PermanentError("period_required")
        records = self.fetch_accounting_rows(period_from, period_to)
        return reconcile_layan_report(
            company,
            records=records,
            period_from=period_from,
            period_to=period_to,
            pending_credits=parse_pending_credits(pending_credits_text),
            min_settlement_difference=min_amount_diff,
        )

    def login(self) -> None:
        creds = get_credentials(PROVIDER_KEY)
        client = LayanClient(credentials=creds)
        try:
            client.force_new_session(creds.username, creds.password)
        except requests.Timeout as exc:
            raise TransientError("timeout") from exc
        except requests.RequestException as exc:
            raise TransientError("connection_error") from exc
        except LayanError as exc:
            raise PermanentError(str(exc)) from exc

    def ping(self) -> None:
        client = self._stored_client()
        try:
            client.ping()
        except LayanSessionError as exc:
            raise AuthRequired("session_expired") from exc
        except requests.Timeout as exc:
            raise TransientError("timeout") from exc
        except requests.RequestException as exc:
            raise TransientError("connection_error") from exc
        except LayanError as exc:
            raise PermanentError(str(exc)) from exc


def _matching_subscription(rows: list, phone: str) -> dict | None:
    wanted = norm_phone(phone)
    for row in rows:
        if not isinstance(row, dict):
            continue
        if norm_phone(str(row.get("number") or "")) == wanted:
            return row
    return None
