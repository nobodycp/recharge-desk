"""Reconcile pages: provider and file failures end in a plain toast, dates kept."""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext

from accounts.models import UserProfile
from companies.models import Company
from providers.contracts import (
    AuthRequired,
    PermanentError,
    TransientError,
)
from providers.crypto import encrypt
from providers.models import ProviderConnection

User = get_user_model()

RECONCILE = "companies.views.provider_service.reconcile"

TIMEOUT_MSG = (
    "Sky took too long to answer. Try a shorter period or run it again in a minute."
)
LOGIN_MSG = (
    "Could not sign in to Sky. Check the connection settings on the company page."
)
CONNECTION_MSG = "Could not reach Sky. Check the internet connection and try again."
UNEXPECTED_MSG = (
    "Matching failed unexpectedly. The details were saved in the server log."
)
UNREADABLE_MSG = (
    "Could not read the file. Make sure it is the Layan charges report "
    "exported as Excel (.xlsx)."
)


class _Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("rec_err_mgr", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )

    def setUp(self):
        self.client.force_login(self.user)

    def messages(self, response) -> list[str]:
        return [str(m) for m in response.context["messages"]]


class SkyReconcileErrorTests(_Base):
    def setUp(self):
        super().setUp()
        connection = ProviderConnection.for_key("sky")
        connection.username = "user"
        connection.secret_encrypted = encrypt("pass")
        connection.save()
        self.company = Company.objects.create(
            name="Sky Co", opening_balance=0, current_balance=0, provider_key="sky"
        )
        self.url = reverse("companies:sky_reconcile", args=[self.company.pk])

    def post(self, error):
        with translation.override("en"), patch(RECONCILE, side_effect=error):
            return self.client.post(
                self.url,
                {
                    "period_from": "2026-08-01",
                    "period_to": "2026-08-31",
                    "min_amount_diff": "3",
                },
            )

    def assert_dates_kept(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="2026-08-01"')
        self.assertContains(response, 'value="2026-08-31"')

    def test_sky_timeout_shows_plain_message(self):
        r = self.post(TransientError("timeout"))
        self.assertEqual(self.messages(r), [TIMEOUT_MSG])
        self.assert_dates_kept(r)

    def test_sky_sync_budget_shows_timeout_message(self):
        r = self.post(TransientError("sync_budget_exceeded"))
        self.assertEqual(self.messages(r), [TIMEOUT_MSG])

    def test_sky_login_failure_shows_plain_message(self):
        r = self.post(AuthRequired("session_expired"))
        self.assertEqual(self.messages(r), [LOGIN_MSG])
        self.assert_dates_kept(r)

    def test_sky_login_code_in_transient_error_shows_login_message(self):
        r = self.post(TransientError("login_failed"))
        self.assertEqual(self.messages(r), [LOGIN_MSG])

    def test_sky_connection_failure_shows_plain_message(self):
        r = self.post(TransientError("connection_error"))
        self.assertEqual(self.messages(r), [CONNECTION_MSG])
        self.assert_dates_kept(r)

    def test_sky_refusal_keeps_scrubbed_reason(self):
        r = self.post(PermanentError("bad period"))
        self.assertEqual(self.messages(r), ["Sky refused the request: bad period"])
        self.assert_dates_kept(r)

    def test_unexpected_error_hides_exception_text(self):
        with self.assertLogs("companies.views", "ERROR") as logs:
            r = self.post(RuntimeError("secret-trace"))
        msgs = self.messages(r)
        self.assertEqual(msgs, [UNEXPECTED_MSG])
        self.assertFalse(any("secret-trace" in m for m in msgs))
        self.assertNotContains(r, "secret-trace")
        self.assertIn("secret-trace", "\n".join(logs.output))
        self.assert_dates_kept(r)


class ArabicMessagesTests(TestCase):
    def test_every_new_message_is_translated(self):
        for msgid in (
            TIMEOUT_MSG,
            LOGIN_MSG,
            CONNECTION_MSG,
            UNEXPECTED_MSG,
            UNREADABLE_MSG,
            "Sky refused the request: %(error)s",
        ):
            with translation.override("ar"):
                self.assertNotEqual(gettext(msgid), msgid, msgid)


class LayanReconcileErrorTests(_Base):
    def setUp(self):
        super().setUp()
        self.company = Company.objects.create(
            name="Layan Co", opening_balance=0, current_balance=0, provider_key="layan"
        )
        self.url = reverse("companies:layan_reconcile", args=[self.company.pk])

    def test_unreadable_layan_file_shows_friendly_error(self):
        upload = SimpleUploadedFile("x.xlsx", b"not excel")
        with translation.override("en"), self.assertLogs("companies.views", "ERROR"):
            r = self.client.post(
                self.url,
                {
                    "period_from": "2026-08-01",
                    "period_to": "2026-08-31",
                    "report_file": upload,
                    "pending_credits": "512823671,29",
                    "min_settlement_difference": "3",
                },
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.messages(r), [UNREADABLE_MSG])
        self.assertContains(r, 'value="2026-08-01"')
        self.assertContains(r, 'value="2026-08-31"')
        self.assertContains(r, "512823671,29")

    def test_unexpected_layan_error_is_logged_not_shown(self):
        upload = SimpleUploadedFile("x.xlsx", b"x")
        with translation.override("en"), patch(
            RECONCILE, side_effect=ValueError("secret-trace")
        ), self.assertLogs("companies.views", "ERROR") as logs:
            r = self.client.post(
                self.url, {"report_file": upload, "min_settlement_difference": "3"}
            )
        self.assertEqual(self.messages(r), [UNREADABLE_MSG])
        self.assertNotContains(r, "secret-trace")
        self.assertIn("secret-trace", "\n".join(logs.output))

    def test_layan_import_error_message_kept(self):
        upload = SimpleUploadedFile("x.xlsx", b"x")
        with translation.override("en"), patch(RECONCILE, side_effect=ImportError("x")):
            r = self.client.post(
                self.url, {"report_file": upload, "min_settlement_difference": "3"}
            )
        self.assertEqual(
            self.messages(r),
            ["Excel support is not installed (openpyxl). Contact your administrator."],
        )
