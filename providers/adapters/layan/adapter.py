from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import BinaryIO

import requests

from providers.adapters.layan.reconcile import (
    LayanReconcileResult,
    parse_pending_credits,
    reconcile_layan_report,
)
from providers.contracts import BaseAdapter, Capability, RawResponse


class LayanAdapter(BaseAdapter):
    key = "layan"
    capabilities = frozenset({Capability.REFRESH, Capability.RECONCILE})
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

    def reconcile(
        self,
        company,
        period_from: date | None,
        period_to: date | None,
        *,
        workbook: BinaryIO,
        min_amount_diff: Decimal | None = None,
        pending_credits_text: str = "",
    ) -> LayanReconcileResult:
        """Match an uploaded Layan charge report against our sales."""
        return reconcile_layan_report(
            company,
            workbook,
            period_from=period_from,
            period_to=period_to,
            pending_credits=parse_pending_credits(pending_credits_text),
            min_settlement_difference=min_amount_diff,
        )
