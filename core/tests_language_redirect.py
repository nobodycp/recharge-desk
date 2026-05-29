"""Tests for default-language prefix redirect middleware."""
from __future__ import annotations

from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings

from core.middleware import DefaultLanguagePrefixRedirectMiddleware
from core.models import AppSettings


@override_settings(LANGUAGE_CODE="en", USE_I18N=True)
class DefaultLanguagePrefixRedirectMiddlewareTests(TestCase):
    def setUp(self):
        AppSettings.objects.update_or_create(pk=1, defaults={"default_language": "ar"})
        self.factory = RequestFactory()
        self.middleware = DefaultLanguagePrefixRedirectMiddleware(
            lambda request: HttpResponse("ok", status=200)
        )

    def test_bare_root_redirects_to_ar_prefix(self):
        request = self.factory.get("/", HTTP_HOST="s.prosim.ps")
        response = self.middleware(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/ar/")

    def test_bare_login_redirects_to_ar_login(self):
        request = self.factory.get("/login/", HTTP_HOST="s.prosim.ps")
        response = self.middleware(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/ar/login/")

    def test_prefixed_path_passes_through(self):
        request = self.factory.get("/ar/login/", HTTP_HOST="s.prosim.ps")
        response = self.middleware(request)
        self.assertEqual(response.status_code, 200)

    def test_healthz_passes_through(self):
        request = self.factory.get("/healthz/", HTTP_HOST="s.prosim.ps")
        response = self.middleware(request)
        self.assertEqual(response.status_code, 200)

    @override_settings(ALLOWED_HOSTS=["rn.prosim.ps", "testserver"])
    def test_public_subdomain_root_is_not_forced_to_ar_prefix(self):
        from phone_refresh.models import SiteSettings

        SiteSettings.objects.update_or_create(
            pk=1,
            defaults={"public_subdomain": "rn.prosim.ps", "redirect_main_to_subdomain": False},
        )
        from phone_refresh.middleware import clear_site_settings_cache

        clear_site_settings_cache()

        request = self.factory.get("/", HTTP_HOST="rn.prosim.ps")
        response = self.middleware(request)
        self.assertEqual(response.status_code, 200)

    @override_settings(LANGUAGE_CODE="ar")
    def test_no_redirect_when_default_matches_language_code(self):
        AppSettings.objects.update_or_create(pk=1, defaults={"default_language": "ar"})
        request = self.factory.get("/", HTTP_HOST="s.prosim.ps")
        response = self.middleware(request)
        self.assertEqual(response.status_code, 200)
