from __future__ import annotations

import requests

from phone_refresh.providers.base import BaseProvider, RawResponse


class AreenProvider(BaseProvider):
    name = "areen"

    URL = "https://api.areen.net/api/common/RefreshMobileNumber"
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Content-Type": "application/json",
        "HX-Current-URL": "https://update.areen.net/",
        "HX-Request": "true",
        "HX-Target": "result",
        "HX-Trigger": "mobileForm",
        "Origin": "https://update.areen.net",
        "Referer": "https://update.areen.net/",
    }

    def call(self, phone: str) -> RawResponse:
        # NOTE: upstream uses ``data=`` (form-encoded) even though the
        # Content-Type header advertises JSON — preserved from the original
        # Flask client because it's what the live endpoint actually accepts.
        try:
            r = requests.post(
                self.URL,
                data={"MobNumber": phone},
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
