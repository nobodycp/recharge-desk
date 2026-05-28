#!/usr/bin/env python3
"""Manual Sky/SmileSim captcha test via Playwright + optional SOCKS proxy.

Typical flow (Mac home IP -> server through SSH reverse tunnel):

  # Mac terminal 1 — local SOCKS proxy (exit via your home IP)
  brew install microsocks
  microsocks -i 127.0.0.1 -p 1081

  # Mac terminal 2 — forward server:1080 -> Mac:1081
  ssh -N -R 127.0.0.1:1080:127.0.0.1:1081 USER@SERVER

  # Server — verify tunnel exits with home IP
  curl --proxy socks5h://127.0.0.1:1080 https://api.ipify.org

  # Server — run this script
  SKY_PLAYWRIGHT_PROXY=socks5://127.0.0.1:1080 \\
    python scripts/test_sky_playwright_proxy.py --phone 0555544071

Inside Docker, 127.0.0.1 is the container. Use host gateway instead, e.g.:
  SKY_PLAYWRIGHT_PROXY=socks5://172.17.0.1:1080
or run with --network host on the host shell first.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from phone_refresh.providers.captcha.firefox_browser import (  # noqa: E402
    FirefoxCaptchaError,
    sky_requests_proxy_kwargs,
    solve_sky_recaptcha_v3,
)

PROVIDERS = {
    "sky": {
        "base": "https://rn.sky-5g.net",
        "api": "https://rn.sky-5g.net/api/public/refresh",
    },
    "smilesim": {
        "base": "https://rn.smilesim.net",
        "api": "https://rn.smilesim.net/api/public/refresh",
    },
}


def _headers(base_url: str) -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Content-Type": "application/json",
        "Origin": base_url,
        "Referer": f"{base_url}/",
    }


def _proxy_requests_kwargs() -> dict:
    return sky_requests_proxy_kwargs()


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Sky reCAPTCHA via Playwright")
    parser.add_argument("--phone", default="0555544071")
    parser.add_argument("--provider", choices=sorted(PROVIDERS), default="sky")
    args = parser.parse_args()

    cfg = PROVIDERS[args.provider]
    proxy = os.environ.get("SKY_PLAYWRIGHT_PROXY", "").strip()
    print("provider:", args.provider)
    print("phone:", args.phone)
    print("SKY_PLAYWRIGHT_PROXY:", proxy or "(none — direct server IP)")

    try:
        ip_resp = requests.get(
            "https://api.ipify.org?format=json",
            timeout=20,
            **_proxy_requests_kwargs(),
        )
        print("exit_ip (requests via proxy):", ip_resp.json())
    except Exception as exc:
        print("exit_ip check failed:", exc)

    try:
        token = solve_sky_recaptcha_v3()
    except FirefoxCaptchaError as exc:
        print("captcha ERROR:", exc)
        return 1

    print("token_len:", len(token))
    print("token_prefix:", token[:60] + "...")

    resp = requests.post(
        cfg["api"],
        json={"phone_number": args.phone, "captcha": token},
        headers=_headers(cfg["base"]),
        timeout=90,
        **_proxy_requests_kwargs(),
    )
    print("sky_http:", resp.status_code)
    try:
        payload = resp.json()
    except ValueError:
        print("sky_body:", resp.text[:500])
        return 1
    print("sky_json:", json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
