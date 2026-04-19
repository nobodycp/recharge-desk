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
    return {
        "bootstrap_css": (
            "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.rtl.min.css"
            if getattr(request, "LANGUAGE_CODE", None) == "ar"
            else "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css"
        ),
        "html_dir": "rtl" if getattr(request, "LANGUAGE_CODE", None) == "ar" else "ltr",
        "languages": settings.LANGUAGES,
        "static_cache_query": static_cache_query,
    }
