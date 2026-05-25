from __future__ import annotations

import requests

from phone_refresh.providers.base import BaseProvider, RawResponse
from phone_refresh.providers.captcha.recaptcha_v3 import RecaptchaV3Bypass


class SkyProvider(BaseProvider):
    name = "sky"

    ANCHOR_URL = (
        "https://www.google.com/recaptcha/api2/anchor"
        "?ar=1&k=6LcMCXYpAAAAABWt8J3o93Z0YRZgbFCd-OfBN5ov"
        "&co=aHR0cHM6Ly9ybi5za3ktNWcubmV0OjQ0Mw.."
        "&hl=en&v=gTpTIWhbKpxADzTzkcabhXN4"
        "&size=invisible&anchor-ms=20000&execute-ms=30000&cb=gyhpito0swjh"
    )
    RELOAD_URL = (
        "https://www.google.com/recaptcha/api2/reload"
        "?k=6LcMCXYpAAAAABWt8J3o93Z0YRZgbFCd-OfBN5ov"
    )
    SUBMIT_URL = "https://rn.sky-5g.net/"
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://rn.sky-5g.net",
        "Referer": "https://rn.sky-5g.net/",
    }

    def call(self, phone: str) -> RawResponse:
        try:
            captcha_token = RecaptchaV3Bypass(self.ANCHOR_URL, self.RELOAD_URL).response()
        except Exception as exc:
            return RawResponse(text="", json=None, status_code=0, error=f"captcha: {exc}")

        try:
            r = requests.post(
                self.SUBMIT_URL,
                data={"captcha": captcha_token, "phoneNumber": phone},
                headers=self.HEADERS,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            return RawResponse(text="", json=None, status_code=0, error=str(exc))

        try:
            payload = r.json()
        except ValueError:
            payload = None

        text = r.text or ""
        stripped = text.lstrip().lower()
        html_form_detected = (
            (stripped.startswith("<!doctype html>") or stripped.startswith("<html"))
            and ("sky telecom" in text.lower() or "recaptcha/api.js" in text.lower())
        )

        return RawResponse(
            text=text,
            json=payload,
            status_code=r.status_code,
            html_form_detected=html_form_detected,
        )
