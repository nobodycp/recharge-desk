from __future__ import annotations

import requests

from phone_refresh.providers.base import BaseProvider, RawResponse


class AlohaProvider(BaseProvider):
    name = "aloha"

    URL = "https://refresh.telecom.co.il/home/refreshNumber"
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://refresh.telecom.co.il",
        "Referer": "https://refresh.telecom.co.il/home",
    }

    def call(self, phone: str) -> RawResponse:
        try:
            r = requests.post(
                self.URL,
                data={"phone_number": phone},
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
