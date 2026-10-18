"""Shared reconcile page: action lists first, search/tabs markup, plain-language numbers.

``build_layan_result`` / ``build_sky_result`` are plain hand-built results (one row in
every bucket) so the page can be rendered without a Layan file or a live Sky call.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from accounts.models import UserProfile
from companies.models import Company
from providers.adapters.layan.reconcile import LayanReconcileResult, ReconcilePhoneRow
from providers.adapters.sky.reconcile import (
    BalanceAnomaly,
    SkyReconcilePhoneRow,
    SkyReconcileResult,
)
from providers.crypto import encrypt
from providers.models import ProviderConnection
from providers.shared.activity import PhoneActivityDetail, SettlementCycleDetail

User = get_user_model()

RECONCILE = "companies.views.provider_service.reconcile"
D = Decimal

MISSING = "0521000001"
EXTRA = "0522000002"
HIGHER = "0531000003"
LOWER = "0531000004"
SETTLEMENT = "0541000005"
MATCHED = "0541000006"
OTHER = "0521000007"


def _lines():
    return [(datetime(2026, 8, 3, 10, 5), D("45"), "Charge")]


def _sales():
    return [
        {
            "cost": D("40"),
            "at": datetime(2026, 8, 3, 10, 0),
            "product": "Cellcom 40",
            "is_esim": True,
        }
    ]


def _detail():
    return PhoneActivityDetail(
        cycles=[
            SettlementCycleDetail(
                charge_amount=D("45"),
                refund_amount=D("-40"),
                retained=D("5"),
                charge_at=datetime(2026, 8, 3, 10, 0),
                refund_at=datetime(2026, 8, 4, 10, 0),
                charge_op="charge",
                refund_op="refund",
            )
        ],
        orphan_refunds=[],
        recharges=[],
        layan_charges=D("45"),
        layan_refunds=D("-40"),
    )


def _row(cls, supplier_kw, phone, category, supplier_net, rd_net, gap, **extra):
    base = dict(
        raw=phone,
        phone=phone,
        rd_net=D(rd_net),
        gap=D(gap),
        category=category,
        category_label=extra.pop("label", category),
        lines=_lines(),
        reason=extra.pop("reason", f"reason for {phone}"),
        rd_sales=_sales(),
    )
    base.update(extra)
    base.update({supplier_kw: D(supplier_net)})
    if cls is ReconcilePhoneRow:
        base.update(layan_charges=D(supplier_net), layan_refunds=D("0"))
    else:
        base.update(sky_charges=D(supplier_net), sky_refunds=D("0"))
    return cls(**base)


def _rows(cls, supplier_kw):
    def make(*args, **kwargs):
        return _row(cls, supplier_kw, *args, **kwargs)

    return dict(
        not_recorded=[make(MISSING, "not_recorded", "45", "0", "45")],
        rd_only=[make(EXTRA, "rd_only", "0", "17.5", "-17.5")],
        logged_other_supplier=[make(OTHER, "other_supplier", "9.1", "9.1", "0")],
        amount_mismatches=[
            make(HIGHER, "mismatch", "45", "40", "5", label="Cost differs"),
            make(LOWER, "mismatch", "40", "46.75", "-6.75", label="Cost differs"),
        ],
        split_settlements=[
            make(
                SETTLEMENT,
                "settlement",
                "8.4",
                "0",
                "8.4",
                activity_detail=_detail(),
                settlement_amount=D("8.4"),
            )
        ],
        matched=[make(MATCHED, "matched", "30", "30", "0")],
    )


def build_layan_result(**overrides):
    kw = dict(
        period_from=date(2026, 8, 1),
        period_to=date(2026, 8, 31),
        layan_end_balance=D("909.45"),
        layan_deposits=D("0"),
        layan_net_movement=D("0"),
        rd_deposits=D("0"),
        rd_deductions=D("0"),
        rd_adjustments=D("0"),
        rd_balance_end=D("912.6"),
        total_not_recorded=D("41.25"),
        total_split_settlements=D("8.4"),
        total_amount_mismatch=D("11.75"),
        total_rd_only=D("17.5"),
        total_logged_other_supplier=D("9.1"),
        estimated_deficit=D("56.35"),
        balance_gap=D("3.15"),
        row_count=137,
        min_settlement_difference=D("3"),
        split_settlements_hidden_count=4,
        **_rows(ReconcilePhoneRow, "layan_net"),
    )
    kw.update(overrides)
    return LayanReconcileResult(**kw)


def build_sky_result(**overrides):
    kw = dict(
        period_from=date(2026, 8, 1),
        period_to=date(2026, 8, 31),
        sky_end_balance=D("909.45"),
        rd_balance_end=D("912.6"),
        balance_anomalies=[
            BalanceAnomaly(
                phone="0541000099",
                at=datetime(2026, 8, 5, 9, 0),
                op="charge",
                before=D("10"),
                delta=D("-5"),
                after=D("7"),
                note="",
            )
        ],
        total_not_recorded=D("41.25"),
        total_split_settlements=D("8.4"),
        total_amount_mismatch=D("11.75"),
        total_rd_only=D("17.5"),
        total_logged_other_supplier=D("9.1"),
        estimated_deficit=D("56.35"),
        row_count=137,
        balance_gap=D("3.15"),
        min_amount_diff=D("3"),
        **_rows(SkyReconcilePhoneRow, "sky_net"),
    )
    kw.update(overrides)
    return SkyReconcileResult(**kw)


class _Base(TestCase):
    provider = ""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("rec_page_mgr", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )

    def setUp(self):
        self.client.force_login(self.user)
        if self.provider in ("sky", "layan"):
            connection = ProviderConnection.for_key(self.provider)
            connection.username = "user"
            connection.secret_encrypted = encrypt("pass")
            connection.save()
        self.company = Company.objects.create(
            name=f"{self.provider} Co",
            opening_balance=0,
            current_balance=0,
            provider_key=self.provider,
        )
        self.url = reverse(f"companies:{self.provider}_reconcile", args=[self.company.pk])

    def result(self):
        raise NotImplementedError

    def template(self):
        return f"companies/{self.provider}_reconcile.html"

    def run_page(self, **result_kwargs):
        payload = {
            "period_from": "2026-08-01",
            "period_to": "2026-08-31",
            "min_amount_diff": "3",
            "min_settlement_difference": "3",
        }
        with translation.override("en"), patch(
            RECONCILE, return_value=(self.result(**result_kwargs), self.template())
        ):
            response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 200)
        return response

    def html(self, **result_kwargs):
        return self.run_page(**result_kwargs).content.decode()

    def tag(self, html, attr):
        """The opening tag that carries ``attr`` (e.g. ``data-rd-rec-panel="matched"``)."""
        match = re.search(r"<[a-z]+\b[^>]*" + re.escape(attr) + r"[^>]*>", html)
        self.assertIsNotNone(match, attr)
        return match.group(0)


class _SharedPageTests:
    """Assertions that hold for both providers."""

    def test_both_action_sections_come_before_tabs(self):
        html = self.html()
        label = self.supplier
        first_tab = html.index("data-rd-rec-tab")
        for heading, marker in (
            (f"At {label}, not in system", 'data-rd-rec-section="missing"'),
            (f"In system, not at {label}", 'data-rd-rec-section="extra"'),
        ):
            self.assertLess(html.index(marker), first_tab, marker)
            self.assertIn(f">{heading}</h2>", html)
            self.assertLess(html.index(f">{heading}</h2>"), first_tab, heading)

    def test_record_sale_link_for_missing_rows(self):
        html = self.html()
        url = reverse("sales:employee_entry")
        self.assertIn(
            f"{url}?company={self.company.pk}&amp;phone={MISSING}&amp;from=reconcile",
            html,
        )

    def test_find_sale_link_for_system_only_rows(self):
        html = self.html()
        self.assertIn(reverse("sales:management_sale_list") + f"?q={EXTRA}", html)

    def test_other_supplier_rows_link_to_the_existing_sale(self):
        html = self.html()
        self.assertIn(reverse("sales:management_sale_list") + f"?q={OTHER}", html)
        self.assertNotIn(f"phone={OTHER}", html)

    def test_matched_panel_hidden_by_default(self):
        html = self.html()
        self.assertRegex(self.tag(html, 'data-rd-rec-panel="matched"'), r"\shidden\b")
        self.assertNotRegex(
            self.tag(html, 'data-rd-rec-panel="mismatch"'), r"\shidden\b"
        )
        self.assertIn('aria-selected="true"', self.tag(html, 'data-rd-rec-tab="mismatch"'))
        self.assertIn('aria-selected="false"', self.tag(html, 'data-rd-rec-tab="matched"'))
        self.assertIn('role="tab"', self.tag(html, 'data-rd-rec-tab="matched"'))
        self.assertIn('role="tabpanel"', self.tag(html, 'data-rd-rec-panel="matched"'))

    def test_every_row_is_searchable_by_phone(self):
        html = self.html()
        for phone in (MISSING, EXTRA, HIGHER, LOWER, SETTLEMENT, MATCHED, OTHER):
            self.assertIn(f'data-phone="{phone}"', html)
        self.assertEqual(html.count("data-rd-rec-row"), self.expected_rows)

    def test_search_markup(self):
        html = self.html()
        search = self.tag(html, "data-rd-rec-search")
        self.assertIn('type="search"', search)
        self.assertIn('inputmode="numeric"', search)
        self.assertIn('placeholder="05XXXXXXXX"', search)
        self.assertIn("Search by number", html)
        self.assertIn('aria-live="polite"', self.tag(html, "data-rd-rec-count"))
        self.assertRegex(self.tag(html, "data-rd-rec-empty"), r"\shidden\b")
        self.assertIn("No number matches.", html)

    def test_every_number_still_shown(self):
        html = self.html()
        r = self.result()
        numbers = [
            r.total_not_recorded,
            r.total_rd_only,
            r.total_amount_mismatch,
            r.total_split_settlements,
            r.total_logged_other_supplier,
            r.estimated_deficit,
            r.balance_gap,
            r.rd_balance_end,
            r.supplier_end_balance,
        ]
        for value in numbers:
            self.assertIn(f"{value:.2f}", html, value)
        self.assertIn("137", html)  # row_count
        self.assertIn("2026-08-01", html)  # period

    def test_summary_strip_totals(self):
        html = self.html()
        # not at supplier = not recorded + logged under another supplier
        self.assertIn("50.35", html)

    def test_gap_has_text_not_only_colour(self):
        html = self.html()
        self.assertIn("Supplier higher", html)
        self.assertIn("System higher", html)
        self.assertIn("+5.00", html)
        self.assertIn("-6.75", html)

    def test_row_details_are_kept(self):
        html = self.html()
        self.assertIn(f"reason for {MISSING}", html)
        self.assertIn(f"Compare {self.supplier} vs system lines", html)
        self.assertIn("System sales", html)
        self.assertIn("Cellcom 40", html)
        self.assertIn("eSIM", html)

    def test_new_settlement_rule_text(self):
        html = self.html()
        self.assertIn(
            "Settlements are charge + disconnect cycles. The retained amount "
            "counts toward the estimated deficit.",
            html,
        )
        self.assertNotIn("they do not increase estimated deficit", html)
        self.assertNotIn("Settlements (not in deficit)", html)
        self.assertNotIn("(unlike Sky)", html)

    def test_no_legacy_styling_left_in_result(self):
        html = self.html()
        self.assertNotIn("table-warning", html)
        self.assertNotIn("table-danger", html)
        self.assertNotIn("table-info", html)
        self.assertNotIn("text-bg-", html)
        self.assertNotIn("<script>\n  (function", html)

    def test_run_button_and_running_status(self):
        html = self.html()
        self.assertIn("Run matching", html)
        self.assertRegex(html, r'role="status"[^>]*>[\s\S]*?Checking…')
        self.assertIn('value="2026-08-01"', html)

    def test_page_script_is_included(self):
        self.assertIn("js/reconcile.js", self.html())

    def test_waiting_card_on_first_visit(self):
        with translation.override("en"):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "rd-rec-steps")
        self.assertContains(response, self.waiting_text)
        self.assertNotContains(response, "data-rd-reconcile")
        self.assertContains(response, "Run matching")
        self.assertContains(response, 'role="status"')
        self.assertContains(response, "Checking…")


class LayanReconcilePageTests(_SharedPageTests, _Base):
    provider = "layan"
    supplier = "Layan"
    waiting_text = "Fetches the Layan accounting report for that period."
    expected_rows = 7

    def result(self, **kwargs):
        return build_layan_result(**kwargs)

    def test_layan_only_numbers_and_notes(self):
        html = self.html()
        self.assertIn("Showing settlements with difference ≥ 3.00 (4 hidden).", " ".join(html.split()))
        self.assertIn("Layan report breakdown", html)
        self.assertIn("Section total", html)
        self.assertIn("Absolute gap total", html)
        self.assertIn("137 report rows", html)
        self.assertNotIn("Sky can take up to a minute.", html)

    def test_end_balance_line(self):
        html = self.html()
        self.assertIn("Balance at report end", html)
        self.assertIn("System", html)

    def test_end_balance_missing_in_file(self):
        html = self.html(layan_end_balance=None)
        self.assertIn("End balance not found in file.", html)
        self.assertNotIn("Balance at report end", html)

    def test_estimated_deficit_rule_under_it(self):
        self.assertIn("Missing sales + retained settlements", self.html())


class SkyReconcilePageTests(_SharedPageTests, _Base):
    provider = "sky"
    supplier = "Sky"
    waiting_text = "Set the date range"
    expected_rows = 8  # seven rows + one balance anomaly

    def result(self, **kwargs):
        return build_sky_result(**kwargs)

    def test_sky_only_numbers_and_notes(self):
        html = self.html()
        self.assertIn("137 Sky rows", html)
        self.assertIn("Balance anomalies", html)
        self.assertIn("0541000099", html)
        self.assertIn("Sky rows where before + delta does not equal after.", html)
        self.assertIn("Sky can take up to a minute.", html)

    def test_estimated_deficit_rule_under_it(self):
        self.assertIn(
            "Missing sales + higher supplier costs + retained settlements", self.html()
        )

    def test_end_balance_line(self):
        html = self.html()
        self.assertIn("Balance at report end", html)
        self.assertIn("909.45", html)

    def test_end_balance_absent_shows_nothing(self):
        html = self.html(sky_end_balance=None)
        self.assertNotIn("Balance at report end", html)
        self.assertNotIn("End balance not found", html)

    def test_sky_template_path_unchanged(self):
        response = self.run_page()
        self.assertTemplateUsed(response, "companies/sky_reconcile.html")
        self.assertTemplateUsed(response, "companies/partials/reconcile_results.html")
        self.assertTemplateUsed(response, "companies/partials/reconcile_rows.html")


class ReconcileContextTests(_Base):
    provider = "layan"

    def test_context_has_provider_and_supplier_label(self):
        with translation.override("en"):
            response = self.client.get(self.url)
        self.assertEqual(response.context["provider"], "layan")
        self.assertEqual(str(response.context["supplier_label"]), "Layan")

    def test_arabic_page_uses_arabic_labels(self):
        self.addCleanup(translation.deactivate)  # LocaleMiddleware leaves "ar" active
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "ar"
        with translation.override("ar"):
            url = reverse("companies:layan_reconcile", args=[self.company.pk])
        with patch(
            RECONCILE,
            return_value=(build_layan_result(), "companies/layan_reconcile.html"),
        ):
            response = self.client.post(
                url,
                {
                    "period_from": "2026-08-01",
                    "period_to": "2026-08-31",
                },
            )
        html = response.content.decode()
        self.assertIn('lang="ar"', html)
        self.assertIn("سجّل البيعة", html)
        self.assertIn("اعرض البيعة", html)
        self.assertIn("جاري الفحص…", html)
        self.assertIn("المزوّد أعلى", html)
        self.assertIn("النظام أعلى", html)
        self.assertIn("لا يوجد رقم مطابق.", html)
        self.assertIn("المبيعات الناقصة + المبالغ المحتجزة من التسويات", html)
