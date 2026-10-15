"""Company detail "General" tab on the page kit.

Covers the period filter moving to `rd_filter_card` + `PeriodFilterForm`
(keeping `cr-period-from` / `cr-period-to` ids and GET names) and the
deposit/adjustment forms moving to `rd_fields` (keeping `dep-submit` /
`adj-submit` names/values and `dep`/`adj` prefixes), plus the KPI labels.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import UserProfile
from companies.models import Company

User = get_user_model()


def _decimal(v):
    return Decimal(str(v))


class CompanyDetailGeneralKitTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("genmgr", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        cls.company = Company.objects.create(
            name="GeneralCo", opening_balance=_decimal(100), current_balance=_decimal(100)
        )

    def setUp(self):
        self.client.force_login(self.user)

    def _get(self, **params):
        params.setdefault("tab", "general")
        return self.client.get(
            reverse("companies:company_detail", args=[self.company.pk]), params
        )

    def test_period_filter_keeps_ids_and_get_names(self):
        r = self._get()
        self.assertContains(r, 'id="cr-period-from"')
        self.assertContains(r, 'id="cr-period-to"')
        self.assertContains(r, 'name="period_from"')
        self.assertContains(r, 'name="period_to"')

    def test_period_filter_uses_kit_filter_card(self):
        r = self._get()
        self.assertContains(r, "rd-filter-card")
        self.assertContains(r, "rd-filter-grid")

    def test_period_filter_round_trips_values(self):
        r = self._get(period_from="2026-08-01", period_to="2026-08-31")
        self.assertContains(r, "2026-08-01")
        self.assertContains(r, "2026-08-31")

    def test_period_filter_hidden_tab_is_kept(self):
        r = self._get()
        self.assertContains(r, 'name="tab"')
        self.assertContains(r, 'value="general"')

    def test_kpi_labels_present(self):
        r = self._get()
        for label in (
            "Opening balance",
            "Total deposits",
            "Consumed (sales)",
            "Current balance",
            "Reversals (cancellations)",
            "Adjustments",
            "Sales count (non-cancelled)",
        ):
            self.assertContains(r, label)

    def test_deposit_and_adjustment_forms_use_kit_fields(self):
        r = self._get()
        self.assertContains(r, "rd-field")
        self.assertNotContains(r, "label_tag")
        self.assertContains(r, 'name="dep-submit"')
        self.assertContains(r, 'value="1"')
        self.assertContains(r, 'name="adj-submit"')

    def test_deposit_post_still_works_with_kit_fields(self):
        resp = self.client.post(
            reverse("companies:company_detail", args=[self.company.pk]),
            data={"tab": "general", "dep-amount": "50.00", "dep-notes": "Top-up", "dep-submit": "1"},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.company.refresh_from_db()
        self.assertEqual(self.company.current_balance, _decimal(150))

    def test_adjustment_post_still_works_with_kit_fields(self):
        resp = self.client.post(
            reverse("companies:company_detail", args=[self.company.pk]),
            data={"tab": "general", "adj-signed_amount": "-10.00", "adj-notes": "Correction", "adj-submit": "1"},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.company.refresh_from_db()
        self.assertEqual(self.company.current_balance, _decimal(90))

    def test_no_app_card_or_bootstrap_card(self):
        r = self._get()
        self.assertNotContains(r, "app-card")
        self.assertNotContains(r, 'class="card')


class PeriodFilterFormTests(TestCase):
    def test_widget_ids_match_kept_contract(self):
        from companies.forms import PeriodFilterForm

        form = PeriodFilterForm()
        self.assertEqual(form.fields["period_from"].widget.attrs.get("id"), "cr-period-from")
        self.assertEqual(form.fields["period_to"].widget.attrs.get("id"), "cr-period-to")
        self.assertFalse(form.fields["period_from"].required)
        self.assertFalse(form.fields["period_to"].required)
