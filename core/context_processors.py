from __future__ import annotations

import os
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, Q


NAV_NOTIFICATIONS_CACHE_TTL = 15
NAV_NOTIFICATIONS_VERSION_KEY = "nav_notif:version"


def bump_nav_notifications_version() -> None:
    """Drop every cached nav badge count at once."""
    try:
        cache.incr(NAV_NOTIFICATIONS_VERSION_KEY)
    except ValueError:
        cache.set(NAV_NOTIFICATIONS_VERSION_KEY, 1, timeout=None)


def _nav_notifications_version() -> int:
    version = cache.get(NAV_NOTIFICATIONS_VERSION_KEY)
    if version is None:
        cache.set(NAV_NOTIFICATIONS_VERSION_KEY, 1, timeout=None)
        return 1
    return int(version)


def _compute_default_asset_buster() -> str:
    """Fingerprint based on the latest mtime of the CSS/JS bundles we ship.

    This guarantees that any change to a tracked static file invalidates
    upstream caches (Nginx, CDN, browsers) without requiring an explicit
    DJANGO_ASSET_CACHE_BUSTER env var on every deploy.
    """
    candidates = [
        "css/design-system.css",
        "css/app.css",
        "js/theme.js",
        "js/data-ui.js",
        "js/employee-payer-assist.js",
    ]
    base_dirs = []
    for d in getattr(settings, "STATICFILES_DIRS", []) or []:
        base_dirs.append(Path(d))
    static_root = getattr(settings, "STATIC_ROOT", None)
    if static_root:
        base_dirs.append(Path(static_root))

    latest_mtime = 0.0
    for base in base_dirs:
        for rel in candidates:
            p = base / rel
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            if mtime > latest_mtime:
                latest_mtime = mtime

    if latest_mtime <= 0:
        return ""
    return str(int(latest_mtime))


_DEFAULT_BUSTER = _compute_default_asset_buster()


def theme(request):
    explicit = getattr(settings, "ASSET_CACHE_BUSTER", "") or ""
    bust = explicit or _DEFAULT_BUSTER
    static_cache_query = f"?v={bust}" if bust else ""
    # Bootstrap CSS is served from /static/vendor/ (see static/vendor/README.md
    # for the pinned upstream versions). The full URL — including the cache
    # buster — is built here so each template just needs `{{ bootstrap_css }}`.
    bootstrap_filename = (
        "vendor/bootstrap.rtl.min.css"
        if getattr(request, "LANGUAGE_CODE", None) == "ar"
        else "vendor/bootstrap.min.css"
    )
    bootstrap_url = f"{settings.STATIC_URL}{bootstrap_filename}{static_cache_query}"
    return {
        "bootstrap_css": bootstrap_url,
        "html_dir": "rtl" if getattr(request, "LANGUAGE_CODE", None) == "ar" else "ltr",
        "languages": settings.LANGUAGES,
        "static_cache_query": static_cache_query,
    }


def compute_nav_notifications(user) -> dict | None:
    """Return ``{awaiting, pending, submissions, total}`` for the given user, or
    ``None`` if the user shouldn't see the notification badge.

    Used by both the context processor (initial render) and the live
    polling endpoint, so they stay in lock-step. One aggregate on ``Sale``
    (awaiting + pending counts) plus one COUNT on submissions. Cached
    briefly so every management page load does not re-run three queries;
    the live poll endpoint still refreshes the badge within seconds.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return None

    try:
        from accounts.permissions import is_management

        if not is_management(user):
            return None

        cache_key = f"nav_notif:v{_nav_notifications_version()}:{user.pk}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        from customers.models import CustomerPaymentSubmission
        from sales.models import Sale

        agg = Sale.objects.aggregate(
            awaiting=Count("id", filter=Q(status=Sale.Status.AWAITING)),
            pending=Count(
                "id",
                filter=Q(status=Sale.Status.PENDING, on_account=False),
            ),
        )
        awaiting = int(agg["awaiting"] or 0)
        pending = int(agg["pending"] or 0)
        submissions = CustomerPaymentSubmission.objects.filter(
            status=CustomerPaymentSubmission.Status.AWAITING
        ).count()
    except Exception:
        return None

    result = {
        "awaiting": awaiting,
        "pending": pending,
        "submissions": submissions,
        "total": awaiting + pending + submissions,
    }
    cache.set(cache_key, result, timeout=NAV_NOTIFICATIONS_CACHE_TTL)
    return result


def nav_notifications(request):
    """Context processor wrapper around :func:`compute_nav_notifications`."""
    return {
        "nav_notifications": compute_nav_notifications(getattr(request, "user", None))
    }


def site_branding(request):
    """Expose the singleton SiteBranding row to every template.

    Local import keeps the context processor import-time cheap and avoids
    a circular import during initial migrations / collectstatic — the
    model only needs to be importable when a request is actually being
    served. ``SiteBranding.load`` caches the singleton for five minutes
    (per gunicorn worker) so the public login page does not hit Postgres
    on every request.

    Also exposes ``site_name`` and ``site_tagline`` separately, falling
    back to translated defaults when the operator hasn't customised them.
    Templates should always use these helpers instead of hard-coding the
    project name so branding stays editable from the admin UI.
    """
    from django.utils.translation import gettext as _g

    default_name = _g("Recharge Desk")
    default_tagline = _g("Management")
    try:
        from core.models import SiteBranding

        instance = SiteBranding.load()
        return {
            "site_branding": instance,
            "site_name": (instance.site_name or "").strip() or default_name,
            "site_tagline": (instance.tagline or "").strip() or default_tagline,
        }
    except Exception:
        # Migrations not applied yet, DB unavailable, etc. — never let a
        # branding lookup break an otherwise-renderable page.
        return {
            "site_branding": None,
            "site_name": default_name,
            "site_tagline": default_tagline,
        }
