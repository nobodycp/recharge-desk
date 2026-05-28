"""Acquire Sky reCAPTCHA v3 tokens via Playwright Firefox."""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

SKY_PAGE_URL = "https://rn.sky-5g.net/"
SITE_KEY = "6LcMCXYpAAAAABWt8J3o93Z0YRZgbFCd-OfBN5ov"
PAGE_ACTION = "public_refresh"

_ALLOWED_HOST_SUFFIXES = (
    "sky-5g.net",
    "google.com",
    "gstatic.com",
    "googleapis.com",
    "recaptcha.net",
)

_GET_TOKEN_JS = """
async () => {
  await new Promise((resolve) => {
    const wait = () => {
      if (window.grecaptcha?.execute) return resolve();
      setTimeout(wait, 30);
    };
    wait();
  });
  await new Promise((resolve) => grecaptcha.ready(resolve));
  return grecaptcha.execute(SITE_KEY, { action: PAGE_ACTION });
}
""".replace("SITE_KEY", repr(SITE_KEY)).replace("PAGE_ACTION", repr(PAGE_ACTION))

_BLOCK_RESOURCE_TYPES = frozenset({"image", "media", "font", "manifest"})

_FIREFOX_PREFS = {
    "permissions.default.image": 2,
    "dom.ipc.processCount": 1,
    "browser.cache.disk.enable": True,
    "browser.cache.memory.enable": True,
    "toolkit.telemetry.enabled": False,
    "datareporting.healthreport.uploadEnabled": False,
    "browser.tabs.animate": False,
    "browser.fullscreen.animateUp": 0,
}

_playwright_executor = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="sky-playwright",
)


class FirefoxCaptchaError(RuntimeError):
    """Raised when Playwright Firefox cannot produce a reCAPTCHA token."""


class _BrowserPool:
    """Reuse one Firefox instance per worker to avoid cold-start latency."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._playwright = None
        self._browser = None
        self._headless: bool | None = None
        self._active_proxy_key: str | None = None
        self._opened_at = 0.0

    @staticmethod
    def _proxy_fingerprint(proxy: dict | None) -> str | None:
        if not proxy:
            return None
        return proxy.get("server")

    def _max_age_sec(self) -> float:
        return float(os.environ.get("SKY_BROWSER_MAX_AGE_SEC", "600"))

    def _close_unlocked(self) -> None:
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        self._headless = None
        self._active_proxy_key = None
        self._opened_at = 0.0

    def acquire(self, *, headless: bool, proxy: dict | None):
        from playwright.sync_api import sync_playwright

        key = self._proxy_fingerprint(proxy)
        with self._lock:
            stale = (
                self._browser is not None
                and self._opened_at > 0
                and (time.monotonic() - self._opened_at) > self._max_age_sec()
            )
            if stale:
                self._close_unlocked()

            if (
                self._browser is not None
                and self._headless == headless
                and self._active_proxy_key == key
                and self._browser.is_connected()
            ):
                return self._playwright, self._browser

            self._close_unlocked()
            self._playwright = sync_playwright().start()
            launch_kwargs: dict = {
                "headless": headless,
                "firefox_user_prefs": _FIREFOX_PREFS,
            }
            if proxy:
                launch_kwargs["proxy"] = proxy
            self._browser = self._playwright.firefox.launch(**launch_kwargs)
            self._headless = headless
            self._active_proxy_key = key
            self._opened_at = time.monotonic()
            return self._playwright, self._browser

    def reset(self) -> None:
        with self._lock:
            self._close_unlocked()


_browser_pool = _BrowserPool()


def _env_bool(name: str, *, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() not in ("0", "false", "no")


def sky_playwright_proxy_url() -> str:
    return os.environ.get("SKY_PLAYWRIGHT_PROXY", "").strip()


def sky_requests_proxy_kwargs() -> dict:
    """Pass-through proxy for Sky API calls (must match captcha exit IP)."""
    proxy = sky_playwright_proxy_url()
    if not proxy:
        return {}
    return {"proxies": {"http": proxy, "https": proxy}}


def _playwright_proxy() -> dict | None:
    raw = sky_playwright_proxy_url()
    if not raw:
        return None
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.hostname:
        raise FirefoxCaptchaError(f"Invalid SKY_PLAYWRIGHT_PROXY={raw!r}")
    port = parsed.port or (1080 if "socks" in parsed.scheme else 8080)
    proxy: dict[str, str] = {"server": f"{parsed.scheme}://{parsed.hostname}:{port}"}
    if parsed.username:
        proxy["username"] = parsed.username
    if parsed.password:
        proxy["password"] = parsed.password
    return proxy


def _host_allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in _ALLOWED_HOST_SUFFIXES)


def _smart_route(route) -> None:
    request = route.request
    if not _host_allowed(request.url) or request.resource_type in _BLOCK_RESOURCE_TYPES:
        route.abort()
    else:
        route.continue_()


def _page_timeout_ms() -> int:
    raw = os.environ.get("SKY_PLAYWRIGHT_PAGE_TIMEOUT_MS", "45000").strip()
    try:
        return max(10_000, int(raw))
    except ValueError as exc:
        raise FirefoxCaptchaError(f"Invalid SKY_PLAYWRIGHT_PAGE_TIMEOUT_MS={raw!r}") from exc


def _extract_token(page, *, wait_sec: float, page_timeout_ms: int) -> str:
    page.route("**/*", _smart_route)
    page.goto(SKY_PAGE_URL, wait_until="commit", timeout=page_timeout_ms)
    if wait_sec > 0:
        time.sleep(wait_sec)
    token = page.evaluate(_GET_TOKEN_JS)
    if not token or not str(token).strip():
        raise FirefoxCaptchaError("grecaptcha.execute returned an empty token")
    return str(token)


def _solve(*, headless: bool, wait_sec: float, page_timeout_ms: int) -> str:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError as exc:
        raise FirefoxCaptchaError(
            "playwright is not installed. Run: pip install playwright && playwright install firefox"
        ) from exc

    proxy = _playwright_proxy()
    try:
        _, browser = _browser_pool.acquire(headless=headless, proxy=proxy)
        page = browser.new_page()
        try:
            return _extract_token(page, wait_sec=wait_sec, page_timeout_ms=page_timeout_ms)
        finally:
            page.close()
    except FirefoxCaptchaError:
        raise
    except Exception:
        _browser_pool.reset()
        raise


def solve_sky_recaptcha_v3(
    *,
    headless: bool | None = None,
    wait_sec: float | None = None,
    page_timeout_ms: int | None = None,
) -> str:
    """Load rn.sky-5g.net in Firefox and return a grecaptcha.execute token."""

    def _run() -> str:
        resolved_headless = headless
        if resolved_headless is None:
            resolved_headless = _env_bool("SKY_PLAYWRIGHT_HEADLESS", default="1")
        resolved_wait_sec = wait_sec
        if resolved_wait_sec is None:
            resolved_wait_sec = float(os.environ.get("SKY_BROWSER_WAIT_SEC", "0"))
        resolved_timeout_ms = page_timeout_ms if page_timeout_ms is not None else _page_timeout_ms()
        try:
            return _solve(
                headless=resolved_headless,
                wait_sec=resolved_wait_sec,
                page_timeout_ms=resolved_timeout_ms,
            )
        except FirefoxCaptchaError:
            raise
        except Exception as exc:
            raise FirefoxCaptchaError(str(exc)) from exc

    timeout_sec = max(90, (page_timeout_ms or _page_timeout_ms()) / 1000 + 30)
    return _playwright_executor.submit(_run).result(timeout=timeout_sec)
