"""OpenBullet-style configurable response-to-status matcher.

Each provider has an ordered list of ``ProviderResponseRule`` rows; the
first rule whose match expression succeeds against the upstream
``RawResponse`` decides the ``RefreshStatus`` returned to the customer.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from phone_refresh.models import MatchType, ProviderResponseRule, RefreshStatus
from phone_refresh.providers.base import RawResponse

log = logging.getLogger(__name__)


def _resolve_json_path(data: Any, expression: str) -> Any:
    """Resolve a JSONPath expression against ``data``.

    Uses ``jsonpath-ng`` when available (full spec); falls back to a tiny
    dot-path resolver (``$.foo.bar`` / ``foo.bar``) so the matcher keeps
    working in environments where the optional dep isn't installed yet.
    """
    expr = expression.strip()
    try:
        from jsonpath_ng.ext import parse  # type: ignore

        matches = parse(expr).find(data)
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0].value
        return [m.value for m in matches]
    except Exception:  # noqa: BLE001 — fall back to dot-path on any jsonpath error
        cursor: Any = data
        path = expr[2:] if expr.startswith("$.") else expr.lstrip("$").lstrip(".")
        if not path:
            return cursor
        for part in path.split("."):
            if cursor is None:
                return None
            if isinstance(cursor, dict):
                cursor = cursor.get(part)
            else:
                return None
        return cursor


def _values_equal(extracted: Any, expected: str) -> bool:
    """Compare a JSONPath result against ``expected`` (string from DB).

    Tries strict string equality first; then numeric equality so a rule
    saved as ``"993"`` still matches an ``int(993)`` from the JSON
    response (and vice-versa).
    """
    if extracted is None:
        return False
    if str(extracted).strip() == expected.strip():
        return True
    try:
        return float(extracted) == float(expected)
    except (TypeError, ValueError):
        return False


def _evaluate_rule(rule: ProviderResponseRule, raw: RawResponse) -> bool:
    match_type = rule.match_type
    pattern = rule.pattern or ""
    expected = rule.expected_value or ""

    if match_type == MatchType.CONTAINS:
        return bool(pattern) and pattern in (raw.text or "")

    if match_type == MatchType.REGEX:
        if not pattern:
            return False
        try:
            return re.search(pattern, raw.text or "") is not None
        except re.error as exc:
            log.warning("Invalid regex in rule %s: %s", rule.pk, exc)
            return False

    if match_type == MatchType.JSON_PATH:
        if raw.json is None or not pattern:
            return False
        extracted = _resolve_json_path(raw.json, pattern)
        return _values_equal(extracted, expected)

    if match_type == MatchType.JSON_PATH_CONTAINS:
        if raw.json is None or not pattern or not expected:
            return False
        extracted = _resolve_json_path(raw.json, pattern)
        if extracted is None:
            return False
        return expected in str(extracted)

    if match_type == MatchType.STATUS_CODE:
        if not expected:
            return False
        try:
            return int(raw.status_code) == int(expected)
        except (TypeError, ValueError):
            return False

    return False


def match_response(
    provider_name: str,
    raw: RawResponse,
) -> tuple[RefreshStatus | None, ProviderResponseRule | None]:
    """Iterate active rules for ``provider_name`` and return the first match.

    Returns ``(None, None)`` when no rule matches; the caller is
    expected to treat that as the ``error`` status.
    """
    rules = (
        ProviderResponseRule.objects.filter(
            provider=provider_name,
            is_active=True,
        )
        .select_related("target_status")
        .order_by("order", "id")
    )

    for rule in rules:
        if _evaluate_rule(rule, raw):
            return rule.target_status, rule
    return None, None
