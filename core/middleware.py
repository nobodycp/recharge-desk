"""Project-specific HTTP middleware.

`SecurityHeadersMiddleware` injects a baseline `Content-Security-Policy`
and `Permissions-Policy` on every response. Django ships
`SecurityMiddleware` which already handles HSTS, X-Content-Type-Options,
Referrer-Policy and Cross-Origin-Opener-Policy — those are configured via
`settings.SECURE_*`. CSP and Permissions-Policy have no built-in support
in Django < 5, so we add them here without pulling in `django-csp`.

The policy is intentionally pragmatic, not strict:

* `default-src 'self'` blocks third-party loads by default.
* `script-src` and `style-src` allow `'unsafe-inline'` because the
  templates ship a meaningful amount of inline `<script>` / `<style>`
  blocks (theme toggle, HTMX error glue, mobile sidebar, etc.). Tightening
  to nonces is a larger refactor (every inline tag must carry
  `{% csp_nonce %}`) and is tracked separately.
* `img-src` allows `data:` so embedded SVG / placeholder data URIs work.
* `frame-ancestors 'none'` is the modern, header-level equivalent of
  `X-Frame-Options: DENY` and is what the spec recommends going forward.
* `object-src 'none'` kills `<object>`/`<embed>` fallbacks (Flash, etc.).

Each directive can be overridden by `CSP_<DIRECTIVE>` settings (e.g.
`CSP_SCRIPT_SRC = ["'self'", "https://cdn.example.com"]`) for sites that
need to extend the defaults without forking this file.

The middleware is a no-op when `settings.SECURITY_HEADERS_ENABLED` is
False (defaults: True). Tests run with it on so we can assert headers
are present.
"""

from __future__ import annotations

from typing import Iterable

from django.conf import settings


_DEFAULT_CSP = {
    "default-src": ["'self'"],
    "script-src": ["'self'", "'unsafe-inline'"],
    "style-src": ["'self'", "'unsafe-inline'"],
    "img-src": ["'self'", "data:"],
    "font-src": ["'self'", "data:"],
    "connect-src": ["'self'"],
    "frame-ancestors": ["'none'"],
    "object-src": ["'none'"],
    "base-uri": ["'self'"],
    "form-action": ["'self'"],
}

_DEFAULT_PERMISSIONS_POLICY = (
    "geolocation=(), "
    "microphone=(), "
    "camera=(), "
    "payment=(), "
    "usb=(), "
    "interest-cohort=()"
)


def _build_csp_header() -> str:
    parts = []
    for directive, default_sources in _DEFAULT_CSP.items():
        setting_name = f"CSP_{directive.upper().replace('-', '_')}"
        sources: Iterable[str] = getattr(settings, setting_name, default_sources)
        if not sources:
            continue
        parts.append(f"{directive} {' '.join(sources)}")
    return "; ".join(parts)


class SecurityHeadersMiddleware:
    """Add CSP and Permissions-Policy to every response."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.enabled = getattr(settings, "SECURITY_HEADERS_ENABLED", True)
        self.csp_header = _build_csp_header()
        self.permissions_policy = getattr(
            settings, "PERMISSIONS_POLICY", _DEFAULT_PERMISSIONS_POLICY
        )

    def __call__(self, request):
        response = self.get_response(request)
        if not self.enabled:
            return response
        if "Content-Security-Policy" not in response:
            response["Content-Security-Policy"] = self.csp_header
        if "Permissions-Policy" not in response:
            response["Permissions-Policy"] = self.permissions_policy
        return response
