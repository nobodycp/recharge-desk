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
from django.utils import translation


# NOTE on third-party origins
# ---------------------------
# Bootstrap, HTMX and Alpine.js are self-hosted under /static/vendor/
# (see static/vendor/README.md), so the only external origin the app
# still depends on is Google Fonts:
#
# * https://fonts.googleapis.com  Cairo / Inter @import stylesheet
# * https://fonts.gstatic.com     the actual woff2 font files
#
# Self-hosting those too would let us drop both lines and run a clean
# `default-src 'self'` policy. That requires bundling the woff2 files
# (4 weights x 2 families = 8 files) and rewriting the @font-face
# stylesheet — tracked separately.
_DEFAULT_CSP = {
    "default-src": ["'self'"],
    # No 'unsafe-eval' on purpose — Alpine.js was the only consumer
    # and was replaced by static/js/nav-toggle.js. 'unsafe-inline'
    # remains because some templates ship small inline <script> tags
    # for per-page boot logic; migrating those to nonces is a separate
    # follow-up.
    "script-src": [
        "'self'",
        "'unsafe-inline'",
    ],
    "style-src": [
        "'self'",
        "'unsafe-inline'",
        "https://fonts.googleapis.com",
    ],
    "img-src": ["'self'", "data:"],
    "font-src": [
        "'self'",
        "data:",
        "https://fonts.gstatic.com",
    ],
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


from django.conf import settings
from django.shortcuts import redirect
from django.utils.translation import get_language_from_path


class DefaultLanguagePrefixRedirectMiddleware:
    """Redirect bare paths to ``/{default_language}/…`` when needed.

    Admin sites configured with Arabic as the operational default sometimes
    still ship ``LANGUAGE_CODE='en'``; only prefixed routes like ``/ar/login/``
    then resolve while ``/`` and ``/login/`` return 404. One redirect fixes
    bookmarks and bare-domain entry without forcing every link to carry a prefix.
    """

    _SKIP_PREFIXES = ("/healthz", "/admin", "/i18n/", "/static/", "/media/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path or "/"
        if any(path.startswith(prefix) for prefix in self._SKIP_PREFIXES):
            return self.get_response(request)
        if get_language_from_path(path) is not None:
            return self.get_response(request)
        if not getattr(settings, "USE_I18N", False):
            return self.get_response(request)

        lang = settings.LANGUAGE_CODE
        try:
            from core.models import AppSettings

            lang = (AppSettings.load().default_language or lang).strip()
        except Exception:
            pass

        if not lang or lang == settings.LANGUAGE_CODE:
            return self.get_response(request)

        return redirect(f"/{lang}{path}")


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


class AppDefaultLanguageMiddleware:
    """Apply admin-configured default language when the user has no language cookie."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.cookie_name = getattr(settings, "LANGUAGE_COOKIE_NAME", "django_language")

    def __call__(self, request):
        if self.cookie_name not in request.COOKIES:
            try:
                from core.models import AppSettings

                lang = AppSettings.load().default_language
                if lang:
                    translation.activate(lang)
                    request.LANGUAGE_CODE = lang
            except Exception:
                pass
        return self.get_response(request)
