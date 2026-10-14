"""Strip provider secrets out of free text before it is logged or stored."""
from __future__ import annotations

import re

_KEY = r"[\w-]*(?:password|jwtToken|sessionId|otpCode|cookie|token)"

# JSON / Python-repr pairs: "key": "value", 'key': 'value', "key": 123
_QUOTED_PAIR = re.compile(
    r"""(?P<pre>(?P<kq>["'])""" + _KEY + r"""(?P=kq)\s*:\s*)"""
    r"""(?P<val>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^,}\]\s]+)""",
    re.IGNORECASE,
)

# Query string / form / header text: key=value, key: value
_BARE_PAIR = re.compile(
    r"""(?P<pre>(?<![\w"'-])""" + _KEY + r"""\s*[=:]\s*)(?P<val>(?!["'*])[^&\s,;}]+)""",
    re.IGNORECASE,
)

# URL userinfo: scheme://user:pass@host
_URL_USERINFO = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^/\s@]+@", re.IGNORECASE)


def _mask_quoted(match: re.Match) -> str:
    val = match.group("val")
    quote = val[0] if val[0] in "\"'" else ""
    return f"{match.group('pre')}{quote}***{quote}"


def scrub(text: str) -> str:
    """Mask secret-looking key values and URL credentials with ``***``."""
    if not text:
        return text or ""
    text = _URL_USERINFO.sub(r"\g<scheme>***@", text)
    text = _QUOTED_PAIR.sub(_mask_quoted, text)
    return _BARE_PAIR.sub(lambda m: f"{m.group('pre')}***", text)
