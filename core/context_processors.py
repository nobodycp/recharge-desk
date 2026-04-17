from django.conf import settings


def theme(request):
    bust = getattr(settings, "ASSET_CACHE_BUSTER", "") or ""
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
