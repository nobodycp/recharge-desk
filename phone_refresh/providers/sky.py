from __future__ import annotations

import os
import time

import requests

from phone_refresh.providers.base import BaseProvider, RawResponse
from phone_refresh.providers.captcha.firefox_browser import solve_sky_recaptcha_v3
from phone_refresh.providers.captcha.recaptcha_v3 import RecaptchaV3Bypass

_SITE_KEY = "6LcMCXYpAAAAABWt8J3o93Z0YRZgbFCd-OfBN5ov"
_ORIGIN_CO = "aHR0cHM6Ly9ybi5za3ktNWcubmV0OjQ0Mw.."
_LEGACY_RECAPTCHA_V = "gTpTIWhbKpxADzTzkcabhXN4"


class SkyProvider(BaseProvider):
    name = "sky"
    timeout: int = 90

    BASE_URL = "https://rn.sky-5g.net"
    SUBMIT_URL = f"{BASE_URL}/api/public/refresh"

    _LEGACY_ANCHOR_URL = (
        "https://www.google.com/recaptcha/api2/anchor"
        f"?ar=1&k={_SITE_KEY}&co={_ORIGIN_CO}"
        f"&hl=en&v={_LEGACY_RECAPTCHA_V}"
        "&size=invisible&anchor-ms=20000&execute-ms=30000&cb=gyhpito0swjh"
    )
    _LEGACY_RELOAD_URL = f"https://www.google.com/recaptcha/api2/reload?k={_SITE_KEY}"

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/json",
        "Origin": BASE_URL,
        "Referer": f"{BASE_URL}/",
    }

    _POLL_INTERVAL_SEC = 0.4
    _POLL_MAX_ATTEMPTS = 40

    def call(self, phone: str) -> RawResponse:
        try:
            captcha_token = self._fetch_captcha_token()
        except Exception as exc:
            return RawResponse(text="", json=None, status_code=0, error=f"captcha: {exc}")

        try:
            response = requests.post(
                self.SUBMIT_URL,
                json={"phone_number": phone, "captcha": captcha_token},
                headers=self.HEADERS,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            return RawResponse(text="", json=None, status_code=0, error=str(exc))

        try:
            payload = response.json()
        except ValueError:
            payload = None

        if isinstance(payload, dict) and payload.get("success") and payload.get("correlation_id"):
            final = self._poll_status(str(payload["correlation_id"]))
            if final is not None:
                payload = final

        text = self._response_text(payload, response.text)
        return RawResponse(
            text=text,
            json=payload,
            status_code=response.status_code,
            html_form_detected=False,
        )

    def _fetch_captcha_token(self) -> str:
        backend = os.environ.get("SKY_CAPTCHA_BACKEND", "firefox").strip().lower()
        if backend == "bypass":
            return RecaptchaV3Bypass(self._LEGACY_ANCHOR_URL, self._LEGACY_RELOAD_URL).response()
        if backend == "firefox":
            return solve_sky_recaptcha_v3()
        raise RuntimeError(
            f"Unsupported SKY_CAPTCHA_BACKEND={backend!r}. Use 'firefox' or 'bypass'."
        )

    def _poll_status(self, correlation_id: str) -> dict | None:
        url = f"{self.SUBMIT_URL}/{correlation_id}/status"
        for _ in range(self._POLL_MAX_ATTEMPTS):
            try:
                response = requests.get(url, headers=self.HEADERS, timeout=self.timeout)
            except requests.RequestException:
                return None
            try:
                data = response.json()
            except ValueError:
                return None
            if data.get("done") or data.get("success") is False:
                return data
            time.sleep(self._POLL_INTERVAL_SEC)
        return None

    @staticmethod
    def _response_text(payload: dict | None, fallback: str) -> str:
        if not payload:
            return fallback
        parts = [fallback]
        for key in ("message", "html", "body", "result"):
            value = payload.get(key)
            if value:
                parts.append(str(value))
        return "\n".join(parts)
