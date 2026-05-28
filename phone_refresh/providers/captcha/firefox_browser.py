"""Acquire Sky reCAPTCHA v3 tokens via Playwright Firefox."""
from __future__ import annotations

import atexit
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

SKY_PAGE_URL = "https://rn.sky-5g.net/"
SITE_KEY = "6LcMCXYpAAAAABWt8J3o93Z0YRZgbFCd-OfBN5ov"
PAGE_ACTION = "public_refresh"

_GET_TOKEN_JS = """
async () => {
  await new Promise((resolve) => {
    const wait = () => {
      if (window.grecaptcha?.execute) return resolve();
      setTimeout(wait, 100);
    };
    wait();
  });
  await new Promise((resolve) => grecaptcha.ready(resolve));
  return grecaptcha.execute(SITE_KEY, { action: PAGE_ACTION });
}
""".replace("SITE_KEY", repr(SITE_KEY)).replace("PAGE_ACTION", repr(PAGE_ACTION))

_BLOCK_RESOURCE_TYPES = frozenset({"image", "media", "font"})

_pool_lock = threading.Lock()
_playwright = None
_browser = None
_browser_uses = 0

# Playwright's sync API runs an asyncio loop on the calling thread. Gunicorn
# ``gthread`` workers reuse those threads for normal Django requests, which
# then hit ``SynchronousOnlyOperation`` when the ORM loads sessions. Keep all
# Playwright work on one dedicated thread per worker process.
_playwright_executor = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="sky-playwright",
)


class FirefoxCaptchaError(RuntimeError):
    """Raised when Playwright Firefox cannot produce a reCAPTCHA token."""


def _env_bool(name: str, *, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() not in ("0", "false", "no")


def _max_browser_uses() -> int:
    return max(1, int(os.environ.get("SKY_BROWSER_MAX_USES", "30")))


def _shutdown_browser_pool_unlocked() -> None:
    global _playwright, _browser, _browser_uses
    if _browser is not None:
        try:
            _browser.close()
        except Exception:
            pass
        _browser = None
    if _playwright is not None:
        try:
            _playwright.stop()
        except Exception:
            pass
        _playwright = None
    _browser_uses = 0


def shutdown_sky_browser_pool() -> None:
    """Close the reused Playwright Firefox instance (tests / worker reload)."""
    if threading.current_thread().name.startswith("sky-playwright"):
        with _pool_lock:
            _shutdown_browser_pool_unlocked()
        return
    _playwright_executor.submit(_shutdown_browser_pool).result(timeout=120)


def _shutdown_browser_pool() -> None:
    with _pool_lock:
        _shutdown_browser_pool_unlocked()


def _atexit_cleanup() -> None:
    with _pool_lock:
        _shutdown_browser_pool_unlocked()
    try:
        _playwright_executor.shutdown(wait=False, cancel_futures=True)
    except TypeError:
        _playwright_executor.shutdown(wait=False)
    except RuntimeError:
        pass


atexit.register(_atexit_cleanup)


def _block_heavy_assets(route) -> None:
    if route.request.resource_type in _BLOCK_RESOURCE_TYPES:
        route.abort()
    else:
        route.continue_()


def _extract_token(page, *, wait_sec: float, page_timeout_ms: int) -> str:
    page.route("**/*", _block_heavy_assets)
    page.goto(SKY_PAGE_URL, wait_until="domcontentloaded", timeout=page_timeout_ms)
    if wait_sec > 0:
        time.sleep(wait_sec)
    token = page.evaluate(_GET_TOKEN_JS)
    if not token or not str(token).strip():
        raise FirefoxCaptchaError("grecaptcha.execute returned an empty token")
    return str(token)


def _acquire_browser(*, headless: bool):
    global _playwright, _browser, _browser_uses

    from playwright.sync_api import sync_playwright

    stale = False
    if _browser is not None:
        try:
            stale = not _browser.is_connected()
        except Exception:
            stale = True
    if stale:
        _browser = None

    if _browser is None or _browser_uses >= _max_browser_uses():
        if _browser is not None:
            try:
                _browser.close()
            except Exception:
                pass
            _browser = None
        if _playwright is None:
            _playwright = sync_playwright().start()
        _browser = _playwright.firefox.launch(headless=headless)
        _browser_uses = 0

    _browser_uses += 1
    return _browser


def _solve_with_fresh_browser(*, headless: bool, wait_sec: float, page_timeout_ms: int) -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.firefox.launch(headless=headless)
        try:
            page = browser.new_page()
            return _extract_token(page, wait_sec=wait_sec, page_timeout_ms=page_timeout_ms)
        finally:
            browser.close()


def _solve_with_reused_browser(*, headless: bool, wait_sec: float, page_timeout_ms: int) -> str:
    with _pool_lock:
        browser = _acquire_browser(headless=headless)
        page = browser.new_page()
        try:
            return _extract_token(page, wait_sec=wait_sec, page_timeout_ms=page_timeout_ms)
        except Exception:
            shutdown_sky_browser_pool()
            raise
        finally:
            try:
                page.close()
            except Exception:
                pass


def solve_sky_recaptcha_v3(
    *,
    headless: bool | None = None,
    wait_sec: float | None = None,
    page_timeout_ms: int = 60_000,
) -> str:
    """Load rn.sky-5g.net in Firefox and return a grecaptcha.execute token."""

    def _solve() -> str:
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError as exc:
            raise FirefoxCaptchaError(
                "playwright is not installed. Add it to requirements and run "
                "'playwright install firefox' in the container."
            ) from exc

        resolved_headless = headless
        if resolved_headless is None:
            resolved_headless = _env_bool("SKY_PLAYWRIGHT_HEADLESS", default="1")
        resolved_wait_sec = wait_sec
        if resolved_wait_sec is None:
            resolved_wait_sec = float(os.environ.get("SKY_BROWSER_WAIT_SEC", "1"))

        reuse = _env_bool("SKY_BROWSER_REUSE", default="1")
        try:
            if reuse:
                return _solve_with_reused_browser(
                    headless=resolved_headless,
                    wait_sec=resolved_wait_sec,
                    page_timeout_ms=page_timeout_ms,
                )
            return _solve_with_fresh_browser(
                headless=resolved_headless,
                wait_sec=resolved_wait_sec,
                page_timeout_ms=page_timeout_ms,
            )
        except FirefoxCaptchaError:
            raise
        except Exception as exc:
            raise FirefoxCaptchaError(str(exc)) from exc

    return _playwright_executor.submit(_solve).result(timeout=max(120, page_timeout_ms / 1000 + 30))
