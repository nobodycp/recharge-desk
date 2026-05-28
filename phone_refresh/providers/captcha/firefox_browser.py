"""Acquire Sky reCAPTCHA v3 tokens via Playwright Firefox."""
from __future__ import annotations

import os
import time

SKY_PAGE_URL = "https://rn.sky-5g.net/"
SITE_KEY = "6LcMCXYpAAAAABWt8J3o93Z0YRZgbFCd-OfBN5ov"
PAGE_ACTION = "public_refresh"

_GET_TOKEN_JS = """
async () => {
  await new Promise((resolve) => {
    const wait = () => {
      if (window.grecaptcha?.execute) return resolve();
      setTimeout(wait, 200);
    };
    wait();
  });
  await new Promise((resolve) => grecaptcha.ready(resolve));
  return grecaptcha.execute(SITE_KEY, { action: PAGE_ACTION });
}
""".replace("SITE_KEY", repr(SITE_KEY)).replace("PAGE_ACTION", repr(PAGE_ACTION))


class FirefoxCaptchaError(RuntimeError):
    """Raised when Playwright Firefox cannot produce a reCAPTCHA token."""


def solve_sky_recaptcha_v3(
    *,
    headless: bool | None = None,
    wait_sec: float | None = None,
    page_timeout_ms: int = 60_000,
) -> str:
    """Load rn.sky-5g.net in Firefox and return a grecaptcha.execute token."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise FirefoxCaptchaError(
            "playwright is not installed. Add it to requirements and run "
            "'playwright install firefox' in the container."
        ) from exc

    if headless is None:
        headless = os.environ.get("SKY_PLAYWRIGHT_HEADLESS", "1").strip() != "0"
    if wait_sec is None:
        wait_sec = float(os.environ.get("SKY_BROWSER_WAIT_SEC", "2"))

    try:
        with sync_playwright() as playwright:
            browser = playwright.firefox.launch(headless=headless)
            try:
                page = browser.new_page()
                page.goto(SKY_PAGE_URL, wait_until="domcontentloaded", timeout=page_timeout_ms)
                time.sleep(wait_sec)
                token = page.evaluate(_GET_TOKEN_JS)
            finally:
                browser.close()
    except Exception as exc:
        raise FirefoxCaptchaError(str(exc)) from exc

    if not token or not str(token).strip():
        raise FirefoxCaptchaError("grecaptcha.execute returned an empty token")
    return str(token)
