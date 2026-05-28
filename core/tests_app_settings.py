"""System settings singleton and wiring tests."""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import UserProfile
from core.models import AppSettings
from customers.services import resolve_or_create_customer_for_sale

User = get_user_model()


class AppSettingsViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("mgr", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.user)

    def test_system_settings_page_renders(self):
        response = self.client.get(reverse("core:system_settings"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "System settings")

    def test_save_persists_toggle(self):
        url = reverse("core:system_settings")
        page = self.client.get(url)
        token = str(page.context["csrf_token"])
        response = self.client.post(
            url,
            {
                "allow_sales_auto_create_customer": "",
                "default_language": "ar",
                "default_theme": "dark",
                "public_default_language": "en",
                "public_default_theme": "light",
                "csrfmiddlewaretoken": token,
            },
        )
        self.assertEqual(response.status_code, 302)
        row = AppSettings.load()
        self.assertFalse(row.allow_sales_auto_create_customer)
        self.assertEqual(row.default_language, "ar")
        self.assertEqual(row.public_default_theme, "light")


class SalesAutoCreateCustomerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("emp", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.EMPLOYEE, "is_active_profile": True},
        )

    def test_blocks_unknown_customer_when_setting_off(self):
        AppSettings.objects.update_or_create(
            pk=1,
            defaults={"allow_sales_auto_create_customer": False},
        )
        with self.assertRaisesMessage(
            ValueError,
            "Create the customer from Customers first.",
        ):
            resolve_or_create_customer_for_sale(
                name="Unknown Person",
                phone="0591234567",
                user=self.user,
            )

    def test_creates_customer_when_setting_on(self):
        AppSettings.objects.update_or_create(
            pk=1,
            defaults={"allow_sales_auto_create_customer": True},
        )
        customer = resolve_or_create_customer_for_sale(
            name="New Person",
            phone="0597654321",
            user=self.user,
        )
        self.assertEqual(customer.name, "New Person")


class AppDefaultLanguageMiddlewareTests(TestCase):
    def test_uses_admin_default_without_language_cookie(self):
        from django.http import HttpResponse
        from django.test import RequestFactory

        from core.middleware import AppDefaultLanguageMiddleware

        AppSettings.objects.update_or_create(pk=1, defaults={"default_language": "ar"})
        seen = {}

        def get_response(request):
            seen["lang"] = request.LANGUAGE_CODE
            return HttpResponse("ok")

        request = RequestFactory().get("/")
        AppDefaultLanguageMiddleware(get_response)(request)
        self.assertEqual(seen["lang"], "ar")

    @override_settings(LANGUAGE_CODE="en")
    def test_skips_override_when_language_cookie_present(self):
        from django.http import HttpResponse
        from django.test import RequestFactory

        from core.middleware import AppDefaultLanguageMiddleware

        AppSettings.objects.update_or_create(pk=1, defaults={"default_language": "ar"})
        seen = {}

        def get_response(request):
            seen["lang"] = request.LANGUAGE_CODE
            return HttpResponse("ok")

        request = RequestFactory().get("/")
        request.COOKIES["django_language"] = "en"
        request.LANGUAGE_CODE = "en"
        AppDefaultLanguageMiddleware(get_response)(request)
        self.assertEqual(seen["lang"], "en")
