"""Tests for public-subdomain middleware routing."""
from __future__ import annotations

from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings

from phone_refresh.middleware import (
    PhoneRefreshSubdomainMiddleware,
    _is_public_refresh_path,
    _is_public_subdomain_root,
    _strip_language_prefix,
)
from phone_refresh.models import SiteSettings


class SubdomainPathHelpersTests(TestCase):
    def test_strip_arabic_prefix(self):
        self.assertEqual(
            _strip_language_prefix("/ar/phone-refresh/api/refresh/"),
            "/phone-refresh/api/refresh/",
        )

    def test_public_refresh_path_with_locale_prefix(self):
        self.assertTrue(_is_public_refresh_path("/ar/phone-refresh/api/refresh/"))

    def test_public_subdomain_root_paths(self):
        self.assertTrue(_is_public_subdomain_root("/"))
        self.assertTrue(_is_public_subdomain_root("/ar"))
        self.assertTrue(_is_public_subdomain_root("/ar/"))
        self.assertFalse(_is_public_subdomain_root("/ar/management/"))


@override_settings(ALLOWED_HOSTS=["rn.prosim.ps", "s.prosim.ps", "testserver"])
class PhoneRefreshSubdomainMiddlewareTests(TestCase):
    def setUp(self):
        SiteSettings.objects.update_or_create(
            pk=1,
            defaults={
                "public_subdomain": "rn.prosim.ps",
                "redirect_main_to_subdomain": False,
            },
        )
        from phone_refresh.middleware import clear_site_settings_cache

        clear_site_settings_cache()

        def get_response(request):
            return HttpResponse(f"path={request.path}", status=200)

        self.middleware = PhoneRefreshSubdomainMiddleware(get_response)
        self.factory = RequestFactory()

    def test_public_api_with_ar_prefix_is_allowed_on_subdomain(self):
        request = self.factory.post("/ar/phone-refresh/api/refresh/")
        request.META["HTTP_HOST"] = "rn.prosim.ps"
        response = self.middleware(request)
        self.assertEqual(response.status_code, 200)
        self.assertIn("/ar/phone-refresh/api/refresh/", response.content.decode())

    def test_management_path_still_blocked_on_subdomain(self):
        request = self.factory.get("/ar/management/")
        request.META["HTTP_HOST"] = "rn.prosim.ps"
        response = self.middleware(request)
        self.assertEqual(response.status_code, 404)

    def test_bare_ar_prefix_serves_public_page_on_subdomain(self):
        request = self.factory.get("/ar/")
        request.META["HTTP_HOST"] = "rn.prosim.ps"
        response = self.middleware(request)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"refresh-api", response.content)
