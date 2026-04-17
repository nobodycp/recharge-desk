from django.conf import settings


def theme(request):
    return {
        "bootstrap_css": (
            "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.rtl.min.css"
            if getattr(request, "LANGUAGE_CODE", None) == "ar"
            else "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css"
        ),
        "html_dir": "rtl" if getattr(request, "LANGUAGE_CODE", None) == "ar" else "ltr",
        "languages": settings.LANGUAGES,
    }
