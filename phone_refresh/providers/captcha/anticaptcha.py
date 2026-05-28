"""Acquire reCAPTCHA v3 tokens via anti-captcha.com (RecaptchaV3TaskProxyless)."""
from __future__ import annotations

import os
import time

import requests

CREATE_TASK_URL = "https://api.anti-captcha.com/createTask"
GET_RESULT_URL = "https://api.anti-captcha.com/getTaskResult"

# Sky public refresh page (must match grecaptcha.execute action on rn.sky-5g.net).
SKY_PAGE_URL = "https://rn.sky-5g.net/"
SKY_SITE_KEY = "6LcMCXYpAAAAABWt8J3o93Z0YRZgbFCd-OfBN5ov"
SKY_PAGE_ACTION = "public_refresh"

# anti-captcha.com only accepts these minScore values for v3.
_ALLOWED_MIN_SCORES = (0.3, 0.7, 0.9)


class AntiCaptchaError(RuntimeError):
    """Raised when anti-captcha.com cannot return a reCAPTCHA token."""


def _api_key() -> str:
    key = os.environ.get("ANTICAPTCHA_API_KEY", "").strip()
    if not key:
        raise AntiCaptchaError(
            "ANTICAPTCHA_API_KEY is not set. Add your anti-captcha.com client key "
            "to the environment (Runtime only in Coolify)."
        )
    return key


def _normalized_min_score(raw: str | None) -> float:
    if not raw or not str(raw).strip():
        return 0.7
    try:
        value = float(str(raw).strip())
    except ValueError as exc:
        raise AntiCaptchaError(f"Invalid SKY_RECAPTCHA_MIN_SCORE={raw!r}") from exc
    return min(_ALLOWED_MIN_SCORES, key=lambda s: abs(s - value))


def _post_json(url: str, payload: dict, *, timeout: int) -> dict:
    try:
        response = requests.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        raise AntiCaptchaError(f"anti-captcha HTTP request failed: {exc}") from exc
    except ValueError as exc:
        raise AntiCaptchaError("anti-captcha returned non-JSON response") from exc

    if data.get("errorId"):
        code = data.get("errorCode") or "unknown"
        desc = data.get("errorDescription") or "anti-captcha error"
        raise AntiCaptchaError(f"{code}: {desc}")
    return data


def solve_recaptcha_v3_anticaptcha(
    *,
    api_key: str | None = None,
    website_url: str | None = None,
    website_key: str | None = None,
    page_action: str | None = None,
    min_score: float | None = None,
    poll_interval_sec: float = 2.0,
    timeout_sec: int = 120,
) -> str:
    """Create a RecaptchaV3TaskProxyless job and return gRecaptchaResponse."""
    resolved_key = api_key or _api_key()
    resolved_url = (website_url or os.environ.get("SKY_RECAPTCHA_WEBSITE_URL") or SKY_PAGE_URL).strip()
    resolved_site_key = (website_key or os.environ.get("SKY_RECAPTCHA_SITE_KEY") or SKY_SITE_KEY).strip()
    resolved_action = (
        page_action or os.environ.get("SKY_RECAPTCHA_PAGE_ACTION") or SKY_PAGE_ACTION
    ).strip()
    if min_score is None:
        resolved_min_score = _normalized_min_score(os.environ.get("SKY_RECAPTCHA_MIN_SCORE"))
    else:
        resolved_min_score = min(_ALLOWED_MIN_SCORES, key=lambda s: abs(s - min_score))

    create_payload = {
        "clientKey": resolved_key,
        "task": {
            "type": "RecaptchaV3TaskProxyless",
            "websiteURL": resolved_url,
            "websiteKey": resolved_site_key,
            "minScore": resolved_min_score,
            "pageAction": resolved_action,
        },
    }
    created = _post_json(CREATE_TASK_URL, create_payload, timeout=30)
    task_id = created.get("taskId")
    if not task_id:
        raise AntiCaptchaError("anti-captcha createTask did not return taskId")

    deadline = time.monotonic() + max(10, timeout_sec)
    result_payload = {"clientKey": resolved_key, "taskId": task_id}
    while time.monotonic() < deadline:
        time.sleep(max(0.5, poll_interval_sec))
        result = _post_json(GET_RESULT_URL, result_payload, timeout=30)
        status = result.get("status")
        if status == "processing":
            continue
        if status != "ready":
            raise AntiCaptchaError(f"anti-captcha unexpected status: {status!r}")

        solution = result.get("solution") or {}
        token = solution.get("gRecaptchaResponse") or solution.get("token")
        if not token or not str(token).strip():
            raise AntiCaptchaError("anti-captcha ready response missing gRecaptchaResponse")
        return str(token).strip()

    raise AntiCaptchaError(f"anti-captcha task {task_id} timed out after {timeout_sec}s")
