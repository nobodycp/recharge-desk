from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from django.contrib import admin
from django.urls import include, path

from core.views import set_language_fixed

urlpatterns = [
    path("admin/", admin.site.urls),
    # Must not live only under i18n_patterns: LocaleMiddleware resets language
    # for un-prefixed paths, which breaks translate_url() inside set_language.
    path("i18n/setlang/", set_language_fixed, name="set_language"),
]

urlpatterns += i18n_patterns(
    path("", include("core.urls")),
    path("", include("accounts.urls")),
    path("", include("companies.urls")),
    path("", include("sales.urls")),
    path("", include("expenses.urls")),
    path("", include("reports.urls")),
    path("", include("customers.urls")),
    prefix_default_language=False,
)

if settings.DEBUG and settings.STATICFILES_DIRS:
    from django.conf.urls.static import static

    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
