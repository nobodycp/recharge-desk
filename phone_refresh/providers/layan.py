from __future__ import annotations

import requests

from phone_refresh.providers.base import BaseProvider, RawResponse


class LayanProvider(BaseProvider):
    name = "layan"

    URL = "https://api.layan-t.net/api/Subscribtions/CustomersRefreshNumber"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:149.0) Gecko/20100101 Firefox/149.0",
        "Accept": "*/*",
        "Content-Type": "application/json",
        "LANG": "ar",
        "Origin": "https://rn.layan-t.net",
        "Referer": "https://rn.layan-t.net/",
    }

    def call(self, phone: str) -> RawResponse:
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
