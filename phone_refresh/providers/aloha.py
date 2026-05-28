from __future__ import annotations

import re

import requests

from phone_refresh.providers.base import BaseProvider, RawResponse

_REFRESH_TOKEN_RE = re.compile(
    r'name=["\']refresh_token["\'][^>]*value=["\']([^"\']+)["\']'
    r'|value=["\']([^"\']+)["\'][^>]*name=["\']refresh_token["\']',
    re.IGNORECASE | re.DOTALL,
)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)


class AlohaProvider(BaseProvider):
    name = "aloha"

    BASE_URL = "https://refresh.telecom.co.il"
    HOME_URL = f"{BASE_URL}/home"
    REFRESH_URL = f"{BASE_URL}/home/refreshNumber"

    _HOME_HEADERS = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-User": "?1",
        "Sec-Fetch-Dest": "document",
    }

    _POST_HEADERS = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": BASE_URL,
        "Referer": HOME_URL,
    }

    def call(self, phone: str) -> RawResponse:
        session = requests.Session()
        try:
            refresh_token = self._fetch_refresh_token(session)
            r = session.post(
                self.REFRESH_URL,
                data={"phone_number": phone, "refresh_token": refresh_token},
                headers=self._POST_HEADERS,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            return RawResponse(text="", json=None, status_code=0, error=str(exc))

        try:
            payload = r.json()
        except ValueError:
            payload = None
        return RawResponse(text=r.text, json=payload, status_code=r.status_code)

    def _fetch_refresh_token(self, session: requests.Session) -> str:
        r = session.get(
            self.HOME_URL,
            headers=self._HOME_HEADERS,
            timeout=self.timeout,
        )
        r.raise_for_status()
        match = _REFRESH_TOKEN_RE.search(r.text)
        if not match:
            raise RuntimeError("Aloha home page did not contain refresh_token")
        return match.group(1) or match.group(2)
