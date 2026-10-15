"""Sale entry GET prefill (quick-record link from the supplier reconcile page)."""
import re
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext

from accounts.models import UserProfile
from companies.models import Company

User = get_user_model()

NOTE_MSGID = (
    "This number was already charged at the supplier. "
    "Record the sale without charging it again."
)


def _reference_input(html):
    """The opening tag of the reference-number input."""
    m = re.search(r"<input\b[^>]*\bid=\"id_reference_number\"[^>]*>", html)
    assert m, "reference input missing"
    return m.group(0)


class EntryPrefillTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(
            name="Alpha Telecom",
            opening_balance=Decimal("1000"),
            current_balance=Decimal("1000"),
        )
        cls.user = User.objects.create_user("prefill_mgr", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )

    def setUp(self):
        self.client.force_login(self.user)
        self.url = reverse("sales:employee_entry")

    def _get(self, **params):
        with translation.override("en"):
            resp = self.client.get(self.url, params, HTTP_ACCEPT_LANGUAGE="en")
        self.assertEqual(resp.status_code, 200)
        return resp

    def test_prefill_normalises_phone(self):
        for raw in ("521234567", "972521234567", "052-123-4567", "0521234567"):
            with self.subTest(raw=raw):
                resp = self._get(company=self.company.pk, phone=raw)
                tag = _reference_input(resp.content.decode())
                self.assertIn('value="0521234567"', tag)

    def test_invalid_phone_is_not_prefilled(self):
        for raw in ("12345", "0569123456", "abc", ""):
            with self.subTest(raw=raw):
                resp = self._get(company=self.company.pk, phone=raw)
                html = resp.content.decode()
                tag = _reference_input(html)
                self.assertNotIn("value=", tag)
                self.assertNotIn("rd-field-error\" id=\"rd-ref-error\">", html)
                self.assertFalse(resp.context["form"].errors)

    def test_prefill_without_company(self):
        resp = self._get(phone="521234567")
        self.assertIn('value="0521234567"', _reference_input(resp.content.decode()))

    def test_reconcile_note_only_when_from_reconcile(self):
        resp = self._get(company=self.company.pk, phone="521234567", **{"from": "reconcile"})
        html = resp.content.decode()
        self.assertIn(NOTE_MSGID, html)
        self.assertRegex(html, r'<div class="alert alert-warning[^"]*" role="note"')
        self.assertTrue(resp.context["from_reconcile"])

        for params in (
            {"company": self.company.pk, "phone": "521234567"},
            {"company": self.company.pk, "phone": "521234567", "from": "other"},
        ):
            with self.subTest(params=params):
                resp = self._get(**params)
                self.assertNotIn(NOTE_MSGID, resp.content.decode())
                self.assertFalse(resp.context["from_reconcile"])

    def test_reconcile_note_arabic_is_compiled(self):
        """Guards a stale or missing compiled .mo for the note."""
        with translation.override("ar"):
            self.assertEqual(
                gettext(NOTE_MSGID),
                "هذا الرقم مشحون عند المزوّد مسبقاً. سجّل البيعة بدون شحنه مرة ثانية.",
            )

    def test_post_ignores_get_phone(self):
        with translation.override("en"):
            resp = self.client.post(
                self.url + "?phone=521234567&from=reconcile",
                {"reference_number": "0501112233", "company": str(self.company.pk)},
                HTTP_ACCEPT_LANGUAGE="en",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["from_reconcile"])
        html = resp.content.decode()
        tag = _reference_input(html)
        self.assertIn('value="0501112233"', tag)
        self.assertNotIn("0521234567", tag)
        self.assertNotIn(NOTE_MSGID, html)
