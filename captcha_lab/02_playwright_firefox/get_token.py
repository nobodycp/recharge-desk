#!/usr/bin/env python3
"""Experiment 02: Playwright Firefox → reCAPTCHA v3 token.

Usage:
  python get_token.py           # headed (recommended)
  python get_token.py --headless
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

SKY_URL = "https://rn.sky-5g.net/"
SITE_KEY = "6LcMCXYpAAAAABWt8J3o93Z0YRZgbFCd-OfBN5ov"
ACTION = "public_refresh"
OUT_FILE = Path(__file__).resolve().parent / "last_token.txt"

GET_TOKEN_JS = """
async () => {
  await new Promise((resolve) => {
    const wait = () => {
      if (window.grecaptcha?.execute) return resolve();
      setTimeout(wait, 200);
    };
    wait();
  });
  await new Promise((resolve) => grecaptcha.ready(resolve));
  const token = await grecaptcha.execute(SITE_KEY, { action: ACTION });
  return {
    length: token.length,
    prefix: token.slice(0, 60),
    webdriver: navigator.webdriver,
    userAgent: navigator.userAgent,
    token,
  };
}
""".replace("SITE_KEY", repr(SITE_KEY)).replace("ACTION", repr(ACTION))


def main() -> int:
    parser = argparse.ArgumentParser(description="Playwright Firefox captcha token")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--wait-sec", type=float, default=2.0)
    args = parser.parse_args()

    print("=== 02 Playwright Firefox ===")
    print("URL:", SKY_URL)
    print("headless:", args.headless)
    print()

    with sync_playwright() as p:
        browser = p.firefox.launch(headless=args.headless)
        context = browser.new_context(
            locale="ar",
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()
        page.goto(SKY_URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(args.wait_sec)
        result = page.evaluate(GET_TOKEN_JS)
        browser.close()

    token = result["token"]
    OUT_FILE.write_text(token, encoding="utf-8")

    print("webdriver:", result.get("webdriver"))
    print("userAgent:", result.get("userAgent", "")[:100])
    print("token length:", result.get("length"))
    print("token prefix:", result.get("prefix"))
    print()
    print("=== TOKEN (copy for Burp) ===")
    print(token)
    print()
    print("Saved:", OUT_FILE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
