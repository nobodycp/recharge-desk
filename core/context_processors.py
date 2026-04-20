import os
from pathlib import Path

from django.conf import settings


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


def site_branding(request):
    """Expose the singleton SiteBranding row to every template.

    Local import keeps the context processor import-time cheap and avoids
    a circular import during initial migrations / collectstatic — the
    model only needs to be importable when a request is actually being
    served. ``cached`` for 5 minutes inside ``SiteBranding.load`` so the
    public login page doesn't hit the DB on every poll.
    """
    try:
        from core.models import SiteBranding

        return {"site_branding": SiteBranding.load()}
    except Exception:
        # Migrations not applied yet, DB unavailable, etc. — never let a
        # branding lookup break an otherwise-renderable page.
        return {"site_branding": None}
