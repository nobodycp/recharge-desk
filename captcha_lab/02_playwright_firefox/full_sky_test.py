#!/usr/bin/env python3
"""Full Sky flow: Playwright Firefox token + JSON API (lab only)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

SKY_URL = "https://rn.sky-5g.net/"
SUBMIT_URL = "https://rn.sky-5g.net/api/public/refresh"
SITE_KEY = "6LcMCXYpAAAAABWt8J3o93Z0YRZgbFCd-OfBN5ov"
ACTION = "public_refresh"
PHONE = sys.argv[1] if len(sys.argv) > 1 else "0555544071"

GET_TOKEN_JS = """
async () => {
  await new Promise(r => grecaptcha.ready(r));
  return grecaptcha.execute(SITE_KEY, { action: ACTION });
}
""".replace("SITE_KEY", repr(SITE_KEY)).replace("ACTION", repr(ACTION))

HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://rn.sky-5g.net",
    "Referer": "https://rn.sky-5g.net/",
    "Accept": "*/*",
}


def get_token(headless: bool = True) -> str:
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=headless)
        page = browser.new_page()
        page.goto(SKY_URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(2)
        token = page.evaluate(GET_TOKEN_JS)
        browser.close()
    return str(token)


def main() -> int:
    print("=== 02 Firefox full Sky flow ===")
    print("phone:", PHONE)
    token = get_token(headless=True)
    print("token length:", len(token))

    r = requests.post(
        SUBMIT_URL,
        json={"phone_number": PHONE, "captcha": token},
        headers=HEADERS,
        timeout=30,
    )
    data = r.json()
    print("Sky POST:", json.dumps(data, ensure_ascii=False, indent=2))

    if data.get("correlation_id"):
        print("PASS: captcha accepted (correlation_id present)")
        return 0
    if "تعذّر التحقق" in str(data.get("message", "")):
        print("FAIL: captcha rejected")
        return 1
    print("Outcome: business rule / cooldown (captcha likely OK)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
