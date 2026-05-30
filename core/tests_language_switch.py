"""End-to-end tests for the language switcher on employee sales entry."""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import UserProfile
from core.models import AppSettings

User = get_user_model()


@override_settings(LANGUAGE_CODE="en", USE_I18N=True)
class EmployeeSalesLanguageSwitchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        AppSettings.objects.update_or_create(pk=1, defaults={"default_language": "ar"})
        cls.user = User.objects.create_user("emp", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.EMPLOYEE, "is_active_profile": True},
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.user)

    def test_switch_to_english_from_arabic_employee_sales(self):
        ar_page = self.client.get("/ar/employee/sales/")
        self.assertEqual(ar_page.status_code, 200)
        self.assertContains(ar_page, "تسجيل آجل")

        switched = self.client.post(
            reverse("set_language"),
            {"language": "en", "next": "/ar/employee/sales/"},
            follow=True,
        )
        self.assertEqual(switched.status_code, 200)
        self.assertContains(switched, "Mark as on account")
        self.assertNotContains(switched, "تسجيل آجل")
        self.assertEqual(switched.request["PATH_INFO"], "/employee/sales/")
        self.assertEqual(self.client.cookies.get("django_language").value, "en")
        self.assertContains(switched, 'data-label-credit-account="Credit account"')
        self.assertContains(switched, 'data-label-cash-payer="Cash payer"')

    def test_switch_back_to_arabic_from_english_employee_sales(self):
        self.client.cookies["django_language"] = "en"
        en_page = self.client.get("/employee/sales/")
        self.assertEqual(en_page.status_code, 200)
        self.assertContains(en_page, "Mark as on account")

        switched = self.client.post(
            reverse("set_language"),
            {"language": "ar", "next": "/employee/sales/"},
            follow=True,
        )
        self.assertEqual(switched.status_code, 200)
        self.assertContains(switched, "تسجيل آجل")
        self.assertEqual(switched.request["PATH_INFO"], "/ar/employee/sales/")
        self.assertEqual(self.client.cookies.get("django_language").value, "ar")
        self.assertContains(switched, 'data-label-credit-account="حساب أجل"')
        self.assertContains(switched, 'data-label-cash-payer="بدون أجل"')
