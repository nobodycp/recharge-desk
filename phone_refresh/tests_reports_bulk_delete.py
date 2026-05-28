"""Bulk delete on phone_refresh reports must work when CSRF cookie is HttpOnly."""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import UserProfile
from phone_refresh.models import PhoneProvider, RefreshLog, RefreshSource, RefreshStatus

User = get_user_model()


class ReportLogsBulkDeleteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("mgr", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        cls.status = RefreshStatus.objects.get(code="error")

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.url = reverse("phone_refresh:report_logs_bulk_delete")

    def _create_log(self, phone: str) -> RefreshLog:
        return RefreshLog.objects.create(
            phone=phone,
            provider=PhoneProvider.SKY,
            source=RefreshSource.WEB,
            status=self.status,
        )

    @override_settings(CSRF_COOKIE_HTTPONLY=True)
    def test_bulk_delete_by_ids_with_csrf(self):
        log = self._create_log("0591111111")
        reports_page = self.client.get(reverse("phone_refresh:reports"))
        token = str(reports_page.context["csrf_token"])
        response = self.client.post(
            self.url,
            data={"mode": "ids", "ids": str(log.pk), "csrfmiddlewaretoken": token},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["deleted"], 1)
        self.assertFalse(RefreshLog.objects.filter(pk=log.pk).exists())

    @override_settings(CSRF_COOKIE_HTTPONLY=True)
    def test_bulk_delete_without_csrf_returns_403(self):
        log = self._create_log("0592222222")
        response = Client(enforce_csrf_checks=True).post(
            self.url,
            data={"mode": "ids", "ids": str(log.pk)},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(RefreshLog.objects.filter(pk=log.pk).exists())
