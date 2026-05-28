"""Host-based routing for the public phone-refresh page.

When an admin configures :attr:`SiteSettings.public_subdomain` from the
"إدارة الموقع" tab (e.g. ``rn.prosim.ps``), this middleware enforces a
strict split between the two hosts:

* On the **public subdomain**, ONLY ``/phone-refresh/...`` (the public
  page + JSON endpoint) and STATIC / MEDIA assets are served. Every
  other path — admin, login, management — returns 404. The bare ``/``
  is served *inline* by the public refresh page view so the subdomain
  "just opens" the refresh form without an extra redirect hop.

* On the **main host** (anything other than the configured subdomain),
  the admin panel keeps working exactly as before. If the optional
  ``redirect_main_to_subdomain`` flag is on AND the request is for
  ``/phone-refresh/``, we 302-redirect to the subdomain so links shared
  on the main host land on the canonical public host.

When ``public_subdomain`` is empty the middleware is a no-op — the
project keeps serving everything on whichever host it receives.

DB lookups during early bootstrap (migrations, ``manage.py check`` on
a fresh DB) are wrapped in a defensive try/except so the middleware
never crashes a request before the table even exists.
"""
from __future__ import annotations

import logging
import time

from django.conf import settings as django_settings
from django.db import DatabaseError
from django.http import HttpResponseNotFound, HttpResponseRedirect

logger = logging.getLogger(__name__)

# Paths that must remain reachable on the public host: the page itself,
# its JSON API, and the asset URLs. We compare with ``startswith`` so
# every nested URL under these prefixes is allowed.
_PUBLIC_PATH_PREFIX = "/phone-refresh/"
# Health probe path. Must remain reachable on every host (including the
# public subdomain) so Coolify / uptime monitors keep getting 200 OK
# regardless of which hostname they hit.
_HEALTHZ_PATH_PREFIX = "/healthz"

# ``SiteSettings`` changes rarely (subdomain routing). A per-process cache
# avoids a Postgres round-trip on every request through this middleware.
_SITE_SETTINGS_CACHE_TTL = 60.0
_site_settings_cache: object | None = None
_site_settings_cached_at: float = 0.0


def clear_site_settings_cache() -> None:
    """Drop the middleware cache after ``SiteSettings`` is saved."""
    global _site_settings_cache, _site_settings_cached_at
    _site_settings_cache = None
    _site_settings_cached_at = 0.0


def _normalize_url_prefix(value: str | None) -> str:
    """Ensure asset URLs are absolute path prefixes (``/static/``)."""
    if not value:
        return ""
    if not value.startswith("/"):
        value = "/" + value
    if not value.endswith("/"):
        value = value + "/"
    return value


def _strip_language_prefix(path: str) -> str:
    """Drop an optional ``/xx/`` locale segment for public-host routing checks."""
    path = path or "/"
    for code, _ in django_settings.LANGUAGES:
        prefix = f"/{code}/"
        if path.startswith(prefix):
            return "/" + path[len(prefix) :]
        if path == f"/{code}":
            return "/"
    return path


def _is_public_refresh_path(path: str) -> bool:
    return _strip_language_prefix(path).startswith(_PUBLIC_PATH_PREFIX)


class PhoneRefreshSubdomainMiddleware:
    """Gate the public refresh page behind a configurable subdomain."""

    def __init__(self, get_response):
        self.get_response = get_response

    def _load_settings(self):
        """Fetch the ``SiteSettings`` singleton, swallowing DB errors.

        The middleware is installed at import time, so we cannot assume
        the table exists — return ``None`` on any DB error and treat it
        like the "no subdomain configured" branch.
        """
        global _site_settings_cache, _site_settings_cached_at

        now = time.monotonic()
        if (
            _site_settings_cache is not None
            and (now - _site_settings_cached_at) < _SITE_SETTINGS_CACHE_TTL
        ):
            return _site_settings_cache

        # Imported here to avoid an app-registry-not-ready error when
        # Django is still wiring up INSTALLED_APPS.
        from phone_refresh.models import SiteSettings

        try:
            settings_obj = SiteSettings.get_solo()
        except DatabaseError:
            return None
        except Exception:  # noqa: BLE001 — defensive: never break the request
            logger.warning("SiteSettings lookup failed", exc_info=True)
            return None

        _site_settings_cache = settings_obj
        _site_settings_cached_at = now
        return settings_obj

    def __call__(self, request):
        # Health probe MUST short-circuit before any DB lookup: it has to
        # answer 200 even when the SiteSettings table does not exist yet
        # (fresh container, migrations still running) and on the public
        # host where this middleware would otherwise 404 everything.
        if (request.path or "").startswith(_HEALTHZ_PATH_PREFIX):
            return self.get_response(request)

        site_settings = self._load_settings()
        if site_settings is None:
            return self.get_response(request)

        configured = (site_settings.public_subdomain or "").strip().lower()
        if not configured:
            # Subdomain feature off → pure pass-through.
            return self.get_response(request)

        host = request.get_host().split(":")[0].lower()
        path = request.path or "/"
        static_prefix = _normalize_url_prefix(django_settings.STATIC_URL)
        media_prefix = _normalize_url_prefix(django_settings.MEDIA_URL)

        if host == configured:
            # ── On the public host: serve ONLY the public refresh
            # surface (+ static / media). Everything else is 404.
            if path == "/" or path == "":
                # Serve the public refresh page directly so the URL stays
                # at the bare subdomain root instead of bouncing through a
                # 302 to /phone-refresh/. Imported lazily to mirror the
                # ``SiteSettings`` import below — avoids any app-registry
                # ordering surprises during early bootstrap.
                from phone_refresh.views.public import public_refresh_page

                return public_refresh_page(request)
            if _is_public_refresh_path(path):
                return self.get_response(request)
            if static_prefix and path.startswith(static_prefix):
                return self.get_response(request)
            if media_prefix and path.startswith(media_prefix):
                return self.get_response(request)
            return HttpResponseNotFound("Not Found")

        # ── Off the public host: optionally redirect /phone-refresh/
        # to the canonical subdomain.
        if (
            site_settings.redirect_main_to_subdomain
            and _is_public_refresh_path(path)
        ):
            target = f"https://{configured}{request.get_full_path()}"
            return HttpResponseRedirect(target)

        return self.get_response(request)
