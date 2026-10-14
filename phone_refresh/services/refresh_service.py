"""High-level refresh orchestrator.

Public entrypoint is :func:`refresh_phone`, which is what both the public
HTTP endpoint and the internal-test admin tool should call.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import logging
import time
from dataclasses import dataclass

from django.utils import timezone

from phone_refresh.models import (
    CustomerMessage,
    PhoneProvider,
    ProviderConfig,
    ProviderResponseRule,
    RefreshLog,
    RefreshSource,
    RefreshStatus,
    SystemSettings,
)
from providers import service as provider_service
from providers.contracts import RawResponse
from providers.registry import ADAPTERS
from providers.sanitize import scrub
from phone_refresh.services.pattern_matcher import match_response

log = logging.getLogger(__name__)

from phone_refresh.validation import PHONE_RE

# Upper bound on how much of the upstream body we persist in
# ``RefreshLog.raw_body``. Sized to comfortably hold a full provider HTML
# page (Sky's longest known response is ~12 KB) while keeping table
# growth bounded if an upstream ever streams something unexpected.
MAX_RAW_BODY_CHARS = 64_000

# Company name → provider key. Case-insensitive prefix match against
# ``sale.company.name``; the first key whose lowercased value is a
# substring of the lowercased company name wins.
COMPANY_TO_PROVIDER: dict[str, str] = {
    "sky": PhoneProvider.SKY.value,
    "aloha": PhoneProvider.ALOHA.value,
    "layan": PhoneProvider.LAYAN.value,
    "areen": PhoneProvider.AREEN.value,
}


@dataclass
class RefreshResult:
    status: RefreshStatus
    provider: str | None
    message_title: str
    message_body: str
    matched_rule_id: int | None
    raw_status_code: int | None
    raw_excerpt: str
    error: str | None = None
    last_refresh_at: _dt.datetime | None = None
    seconds_since_last_refresh: int | None = None
    # Full upstream body kept in-memory only (never persisted). Used by the
    # admin diagnostic panel so reviewers can see the entire response, not
    # just the truncated excerpt that goes into RefreshLog.
    raw_body_full: str = ""
    html_form_detected: bool = False


def _lookup_provider_for_phone(phone: str) -> str | None:
    """Resolve which upstream provider should handle ``phone`` via the most
    recent ``Sale`` row whose reference number equals it.

    Imported lazily so the ``phone_refresh`` app can still be loaded in
    environments where ``sales`` isn't installed (e.g. very early in
    migrations).
    """
    from sales.models import Sale

    sale = (
        Sale.objects.filter(reference_number=phone)
        .select_related("company")
        .order_by("-created_at")
        .first()
    )
    if not sale or not sale.company:
        return None

    provider = (getattr(sale.company, "phone_refresh_provider", None) or "").strip()
    if provider:
        return provider

    name = (sale.company.name or "").strip().lower()
    if not name:
        return None
    for needle, provider_key in COMPANY_TO_PROVIDER.items():
        if needle in name:
            return provider_key
    return None


def _S(code: str) -> RefreshStatus:
    """Resolve a ``RefreshStatus`` code → model instance (cached)."""
    return RefreshStatus.get_by_code(code)


def _last_successful_refresh(phone: str) -> RefreshLog | None:
    return (
        RefreshLog.objects.filter(phone=phone, status__code="refreshed")
        .order_by("-created_at")
        .first()
    )


def _lookup_message(status: RefreshStatus) -> tuple[str, str]:
    msg = CustomerMessage.objects.filter(status=status).first()
    if msg is None:
        return ("", status.label)
    return (msg.title or "", msg.body or "")


def _normalize_raw_text(raw: RawResponse | None) -> str:
    """Return the full upstream body as text, falling back to JSON dump."""
    if raw is None:
        return ""
    text = raw.text or ""
    if not text and raw.json is not None:
        try:
            import json as _json

            text = _json.dumps(raw.json, ensure_ascii=False)
        except (TypeError, ValueError):
            text = ""
    return text


def _build_excerpt(raw: RawResponse | None, limit: int = 500) -> str:
    """Storage-truncated excerpt for ``RefreshLog.raw_excerpt``."""
    return _normalize_raw_text(raw)[:limit]


def human_elapsed_arabic(seconds: float) -> str:
    """Format a duration in seconds as an Arabic short string.

    Examples (positive integers only; negatives clamp to 0):
        120     → "2 دقيقة"
        3 600   → "1 ساعة"
        8 030   → "2 ساعات و 13 دقيقة"
    """
    seconds = max(0, int(seconds))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours <= 0:
        return f"{minutes} دقيقة"
    hour_word = "ساعة" if hours == 1 else "ساعات"
    if minutes <= 0:
        return f"{hours} {hour_word}"
    return f"{hours} {hour_word} و {minutes} دقيقة"


_WAIT_FALLBACK_BODY = "يرجى الانتظار قبل تحديث الرقم مرة أخرى."


def _interpolate_body(body: str, *, elapsed: str | None) -> str:
    """Replace known ``{placeholder}`` tokens in a customer message body.

    Uses plain ``str.replace`` (not ``str.format``) so stray braces in
    admin-edited text can never raise.

    When the body references ``{elapsed}`` but no elapsed value is
    available (e.g. the provider itself returned a wait pattern rather
    than the local cooldown kicking in), fall back to a short generic
    wait sentence instead of leaving a dangling "مضى على آخر تحديث: ."
    artifact.
    """
    if not body:
        return body
    if "{elapsed}" not in body:
        return body
    if elapsed:
        return body.replace("{elapsed}", elapsed)
    return _WAIT_FALLBACK_BODY


_KNOWN_SOURCES = frozenset(
    {
        RefreshSource.API,
        RefreshSource.WEB,
        RefreshSource.EMPLOYEE,
        RefreshSource.SMS,
        RefreshSource.INTERNAL_TEST,
    }
)


def normalize_source(source: str) -> str:
    """Callers may pass an unknown string but we must never persist anything
    outside the known choices. Unknown values fall back to ``web`` (the
    safest "real traffic" bucket) and ``legacy`` is explicitly rejected for
    new rows.
    """
    if source == RefreshSource.LEGACY or source not in _KNOWN_SOURCES:
        return RefreshSource.WEB
    return source


def precheck(
    phone: str,
    *,
    ip: str | None,
    internal_test: bool = False,
    forced_provider: str | None = None,
    bypass_provider_off: bool = False,
    source: str = RefreshSource.WEB,
) -> tuple[RefreshResult | None, str]:
    """Everything that can be decided without calling the provider.

    Returns ``(result, "")`` when the request is answered immediately (the
    result is already logged), or ``(None, provider_key)`` when the caller
    must go on to call the provider.

    When ``internal_test`` is ``True`` (admin-only path), the service-off
    toggle and the per-phone cooldown are both bypassed so admins can
    always exercise the live providers.

    ``forced_provider`` lets admin tools call a specific provider directly,
    skipping the company/DB-precheck flow entirely. ``bypass_provider_off``
    additionally ignores the per-provider on/off toggle so admins can
    test a disabled provider.
    """
    started = time.monotonic()
    phone = (phone or "").strip()
    settings = SystemSettings.get()
    source = normalize_source(source)
    skip_cooldown = internal_test

    # 1. Service-off short-circuit (skipped during internal test only).
    if not settings.service_enabled and not internal_test:
        return (
            _record_and_return(
                phone=phone,
                provider_key="",
                status=_S("service_off"),
                raw=None,
                matched_rule=None,
                ip=ip,
                started=started,
                settings=settings,
                internal_test=internal_test,
                source=source,
            ),
            "",
        )

    # 2. Phone validation.
    if not PHONE_RE.match(phone):
        return (
            _record_and_return(
                phone=phone,
                provider_key="",
                status=_S("not_found"),
                raw=None,
                matched_rule=None,
                ip=ip,
                started=started,
                error="invalid_phone",
                settings=settings,
                internal_test=internal_test,
                source=source,
            ),
            "",
        )

    # 3. Cooldown (skipped during internal test only).
    last_log = _last_successful_refresh(phone)
    if not skip_cooldown and last_log:
        elapsed_sec = (timezone.now() - last_log.created_at).total_seconds()
        if elapsed_sec < settings.cooldown_seconds:
            return (
                _record_and_return(
                    phone=phone,
                    provider_key="",
                    status=_S("wait"),
                    raw=None,
                    matched_rule=None,
                    ip=ip,
                    started=started,
                    elapsed_sec=elapsed_sec,
                    last_log=last_log,
                    settings=settings,
                    internal_test=internal_test,
                    source=source,
                ),
                "",
            )

    # 4. Provider routing.
    if forced_provider:
        provider_key = forced_provider
    elif settings.db_precheck_enabled:
        provider_key = _lookup_provider_for_phone(phone)
        if not provider_key:
            return (
                _record_and_return(
                    phone=phone,
                    provider_key="",
                    status=_S("not_found"),
                    raw=None,
                    matched_rule=None,
                    ip=ip,
                    started=started,
                    error="no_company_match",
                    last_log=last_log,
                    settings=settings,
                    internal_test=internal_test,
                    source=source,
                ),
                "",
            )
    else:
        provider_key = settings.default_provider or ""
        if not provider_key:
            return (
                _record_and_return(
                    phone=phone,
                    provider_key="",
                    status=_S("error"),
                    raw=None,
                    matched_rule=None,
                    ip=ip,
                    started=started,
                    error="db_precheck_off_and_no_default_provider",
                    last_log=last_log,
                    settings=settings,
                    internal_test=internal_test,
                    source=source,
                ),
                "",
            )

    # 4b. Per-provider on/off toggle (skipped on admin bypass).
    if not bypass_provider_off and not ProviderConfig.is_provider_enabled(provider_key):
        return (
            _record_and_return(
                phone=phone,
                provider_key=provider_key,
                status=_S("provider_off"),
                raw=None,
                matched_rule=None,
                ip=ip,
                started=started,
                error="provider_disabled",
                last_log=last_log,
                settings=settings,
                internal_test=internal_test,
                source=source,
            ),
            "",
        )

    # 4c. Unknown provider key (checked here so a job is never queued for it).
    if provider_key not in ADAPTERS:
        return (
            _record_and_return(
                phone=phone,
                provider_key=provider_key,
                status=_S("error"),
                raw=None,
                matched_rule=None,
                ip=ip,
                started=started,
                error=f"no_provider:{provider_key}",
                last_log=last_log,
                settings=settings,
                internal_test=internal_test,
                source=source,
            ),
            "",
        )

    return None, provider_key


def _scrubbed(raw: RawResponse) -> RawResponse:
    """Copy of ``raw`` that is safe to persist or show (secrets masked)."""
    return dataclasses.replace(
        raw, text=scrub(_normalize_raw_text(raw)), json=None
    )


def complete(
    *,
    phone: str,
    provider_key: str,
    raw: RawResponse,
    ip: str | None,
    source: str,
    started: float,
    internal_test: bool = False,
) -> RefreshResult:
    """Turn a provider response into a logged, customer-facing result."""
    phone = (phone or "").strip()
    settings = SystemSettings.get()
    source = normalize_source(source)
    last_log = _last_successful_refresh(phone)
    safe_raw = _scrubbed(raw)

    if raw.error:
        return _record_and_return(
            phone=phone,
            provider_key=provider_key,
            status=_S("error"),
            raw=safe_raw,
            matched_rule=None,
            ip=ip,
            started=started,
            error=scrub(raw.error),
            last_log=last_log,
            settings=settings,
            internal_test=internal_test,
            source=source,
        )

    # NOTE: Previously, we short-circuited here when ``raw.html_form_detected``
    # was true, assuming the HTML body meant the reCAPTCHA was rejected.
    # That assumption was incorrect: Sky's normal response IS an HTML page,
    # with the actual state signal embedded inside a
    # ``<div class="notifications">`` block as plain Arabic text. The pattern
    # matcher handles that case directly, so we let the response flow into
    # ``match_response`` unchanged. The ``html_form_detected`` flag is kept
    # as a diagnostic signal (surfaced via admin/logs) but no longer has any
    # behavioural effect.

    # Match on the original response; only the persisted copy is scrubbed.
    status, matched_rule = match_response(provider_key, raw)
    if status is None:
        status = _S("error")

    return _record_and_return(
        phone=phone,
        provider_key=provider_key,
        status=status,
        raw=safe_raw,
        matched_rule=matched_rule,
        ip=ip,
        started=started,
        last_log=last_log,
        settings=settings,
        internal_test=internal_test,
        source=source,
    )


def complete_failure(
    *,
    phone: str,
    provider_key: str,
    error: str,
    ip: str | None,
    source: str,
    started: float,
    internal_test: bool = False,
    status_code: str = "error",
) -> RefreshResult:
    """Log + answer a call that never produced a provider response.

    Defaults to the generic ``error`` status (provider crashed); the queue
    uses ``status_code="busy"`` when a job cannot even be enqueued.
    """
    phone = (phone or "").strip()
    return _record_and_return(
        phone=phone,
        provider_key=provider_key,
        status=_S(status_code),
        raw=None,
        matched_rule=None,
        ip=ip,
        started=started,
        error=scrub(error),
        last_log=_last_successful_refresh(phone),
        settings=SystemSettings.get(),
        internal_test=internal_test,
        source=normalize_source(source),
    )


def refresh_phone(
    phone: str,
    ip: str | None = None,
    *,
    internal_test: bool = False,
    forced_provider: str | None = None,
    bypass_provider_off: bool = False,
    source: str = RefreshSource.WEB,
) -> RefreshResult:
    """End-to-end synchronous orchestrator: validate, route, call, match, log, respond."""
    started = time.monotonic()
    phone = (phone or "").strip()
    immediate, provider_key = precheck(
        phone,
        ip=ip,
        internal_test=internal_test,
        forced_provider=forced_provider,
        bypass_provider_off=bypass_provider_off,
        source=source,
    )
    if immediate is not None:
        return immediate

    try:
        raw = provider_service.refresh(provider_key, phone)
    except Exception as exc:  # noqa: BLE001 — provider errors must not bubble up
        log.exception("provider %s crashed for phone %s", provider_key, phone)
        return complete_failure(
            phone=phone,
            provider_key=provider_key,
            error=str(exc),
            ip=ip,
            source=source,
            started=started,
            internal_test=internal_test,
        )

    return complete(
        phone=phone,
        provider_key=provider_key,
        raw=raw,
        ip=ip,
        source=source,
        started=started,
        internal_test=internal_test,
    )


def status_payload(status_code: str, **extra) -> dict:
    """Payload carrying just the seeded customer message of ``status_code``."""
    title, body = _lookup_message(_S(status_code))
    return {"status": status_code, "message": {"title": title, "body": body}, **extra}


def result_payload(result: RefreshResult) -> dict:
    """JSON body returned to web/API/employee/SMS callers for ``result``."""
    payload = {
        "status": result.status.code,
        "message": {"title": result.message_title, "body": result.message_body},
    }
    # Only expose last_refresh fields when the admin toggle is on. The
    # service already nulls them out when off, but we double-check here so
    # the response shape is unambiguous.
    if SystemSettings.get().show_last_refresh:
        payload["last_refresh_at"] = (
            result.last_refresh_at.isoformat()
            if result.last_refresh_at is not None
            else None
        )
        payload["seconds_since_last_refresh"] = result.seconds_since_last_refresh
    return payload


# Keys an internal-test job result carries for the admin diagnostics panels
# on top of ``result_payload``. They must never reach customers/employees:
# ``refresh_jobs.job_payload`` strips them.
INTERNAL_RESULT_KEYS = ("raw_excerpt", "diagnostics")
RAW_EXCERPT_MAX_CHARS = 500


def internal_result_extras(result: RefreshResult) -> dict:
    """Admin-only diagnostics for ``result`` (scrubbed, excerpt capped)."""
    return {
        # Scrub the whole body first, then cap: a secret straddling the cap
        # must be masked before it can be cut in half.
        "raw_excerpt": scrub(result.raw_body_full or result.raw_excerpt or "")[
            :RAW_EXCERPT_MAX_CHARS
        ],
        "diagnostics": {
            "provider": result.provider,
            "matched_rule_id": result.matched_rule_id,
            "raw_status_code": result.raw_status_code,
            "html_form_detected": result.html_form_detected,
            "raw_body_length": len(result.raw_body_full or ""),
            "error": scrub(result.error) if result.error else None,
        },
    }


def _record_and_return(
    *,
    phone: str,
    provider_key: str,
    status: RefreshStatus,
    raw: RawResponse | None,
    matched_rule: ProviderResponseRule | None,
    ip: str | None,
    started: float,
    error: str | None = None,
    elapsed_sec: float | None = None,
    last_log: RefreshLog | None = None,
    settings: SystemSettings | None = None,
    internal_test: bool = False,
    message_override: tuple[str, str] | None = None,
    source: str = RefreshSource.WEB,
) -> RefreshResult:
    if message_override is not None:
        title, body = message_override
    else:
        title, body = _lookup_message(status)
    elapsed_str = (
        human_elapsed_arabic(elapsed_sec) if elapsed_sec is not None else None
    )
    body = _interpolate_body(body, elapsed=elapsed_str)

    duration_ms = int((time.monotonic() - started) * 1000)
    full_body = _normalize_raw_text(raw)
    excerpt = full_body[:500]
    # Storage policy: only persist the full upstream body when it's
    # actually useful for diagnostics — i.e. no provider rule matched
    # (unexpected upstream output) or the resolved status is ``error``
    # (technical/transport failure). Clean rule hits keep the 500-char
    # excerpt only, which is enough for the reports list at-a-glance
    # and keeps table growth bounded.
    should_store_full = (matched_rule is None) or (status.code == "error")
    stored_body = full_body[:MAX_RAW_BODY_CHARS] if should_store_full else ""

    try:
        RefreshLog.objects.create(
            phone=phone[:20],
            provider=provider_key,
            status=status,
            source=source,
            raw_status_code=(raw.status_code if raw else None),
            raw_excerpt=excerpt,
            raw_body=stored_body,
            matched_rule=matched_rule,
            duration_ms=duration_ms,
            ip=ip,
        )
    except Exception:  # noqa: BLE001 — never let logging break the response
        log.exception("RefreshLog write failed for phone %s", phone)

    last_refresh_at: _dt.datetime | None = None
    seconds_since: int | None = None
    if settings and settings.show_last_refresh and last_log is not None:
        last_refresh_at = last_log.created_at
        seconds_since = int(
            max(0, (timezone.now() - last_log.created_at).total_seconds())
        )

    return RefreshResult(
        status=status,
        provider=provider_key or None,
        message_title=title,
        message_body=body,
        matched_rule_id=matched_rule.pk if matched_rule else None,
        raw_status_code=raw.status_code if raw else None,
        raw_excerpt=excerpt,
        error=error,
        last_refresh_at=last_refresh_at,
        seconds_since_last_refresh=seconds_since,
        raw_body_full=full_body,
        html_form_detected=bool(raw.html_form_detected) if raw else False,
    )
