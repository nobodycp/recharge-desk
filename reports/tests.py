"""Tests for the reports app: dashboard KPIs and report views."""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserProfile
from companies.models import Company, Product, ProductLine
from customers.models import Customer
from customers.services import create_customer
from expenses.models import Expense
from reports.models import PhoneLineTracking
from sales.models import PaymentMethod, Sale
from sales.services import create_sale, mark_sale_paid

User = get_user_model()


def _d(v):
    return Decimal(str(v))


class _ReportsBase(TestCase):
    """Shared fixtures and helpers for reports tests."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("mgr_rep", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )

        cls.company = Company.objects.create(
            name="ReportCo",
            opening_balance=_d(10_000),
            current_balance=_d(10_000),
        )
        cls.line = ProductLine.objects.create(company=cls.company, name="L")
        cls.product = Product.objects.create(
            line=cls.line,
            variant_label="P",
            cost_price=_d(5),
            default_sell_price=_d(20),
        )
        cls.cash = PaymentMethod.objects.create(name="Cash")

    def setUp(self):
        # Django's LocMemCache is a single dict shared across the test
        # process. Without this clear, KPI values cached by an earlier
        # test (e.g. dashboard reloads after creating sales) leak into
        # the next test which expects a fresh database.
        cache.clear()
        self.client = Client()
        self.client.force_login(self.user)

    def _make_sale(
        self,
        *,
        sell=20,
        paid=False,
        on_account=False,
        customer=None,
        ref=None,
        payer="Ali",
        is_esim=False,
    ):
        s = create_sale(
            company=self.company,
            product=self.product,
            reference_number=ref or f"R-{Sale.objects.count() + 1}",
            payer_name=payer,
            payment_method=None if on_account else self.cash,
            sell_price_actual=_d(sell),
            notes="",
            user=self.user,
            on_account=on_account,
            customer=customer,
            is_esim=is_esim,
        )
        if paid and not on_account:
            mark_sale_paid(sale=s, user=self.user)
        return s


# ============================================================ Dashboard
class DashboardSmokeTests(_ReportsBase):
    def test_empty_dashboard_renders(self):
        r = self.client.get(reverse("reports:dashboard"))
        self.assertEqual(r.status_code, 200)
        ctx = r.context
        self.assertEqual(ctx["pending_count"], 0)
        self.assertEqual(ctx["awaiting_count"], 0)
        self.assertEqual(ctx["customer_debt_total"], 0)
        self.assertEqual(ctx["today_count"], 0)
        self.assertEqual(ctx["today_volume"] or 0, 0)
        self.assertEqual(ctx["today_profit"] or 0, 0)
        self.assertEqual(ctx["total_profit"] or 0, 0)
        self.assertEqual(ctx["esim_sales_count"], 0)


class DashboardKPITests(_ReportsBase):
    """KPI numbers must match underlying aggregates after creating fixtures."""

    def setUp(self):
        super().setUp()
        # 2 paid sales today (profit each = 20-5 = 15), 1 pending sale today
        self.paid1 = self._make_sale(sell=20, paid=True)
        self.paid2 = self._make_sale(sell=20, paid=True)
        self.pending = self._make_sale(sell=20)  # PENDING
        self.cancelled = self._make_sale(sell=20)
        self.cancelled.status = Sale.Status.CANCELLED
        self.cancelled.save(update_fields=["status"])

        # On-account / awaiting (debt request)
        cust = create_customer(name="Hazem", user=self.user)
        self._make_sale(sell=200, on_account=True, customer=cust, payer="Hazem")

        # Today loss (sell=0)
        self._make_sale(sell=0, paid=True)

        # Some customer debt (after settlement, balance may be < or > 0)
        cust.current_balance = _d(50)
        cust.save(update_fields=["current_balance"])

        # Expenses
        Expense.objects.create(
            title="rent", category="ops", amount=_d(100),
            date=timezone.localdate(), created_by=self.user,
        )

    def test_kpis_match_aggregates(self):
        r = self.client.get(reverse("reports:dashboard"))
        self.assertEqual(r.status_code, 200)
        ctx = r.context

        # pending_count = sales with status=PENDING and on_account=False
        # paid sales are PAID, awaiting is AWAITING, cancelled is CANCELLED.
        # Only `self.pending` matches.
        self.assertEqual(ctx["pending_count"], 1)

        # awaiting_count = on-account sale created above
        self.assertEqual(ctx["awaiting_count"], 1)

        # Customer debt total = sum of customers with current_balance > 0
        self.assertEqual(ctx["customer_debt_total"], _d(50))

        # Today volume includes paid + pending (confirmed_sales: excludes cancelled,
        # awaiting, written-off). The zero-priced loss sale also counts in volume.
        # paid1+paid2+pending+loss = 20+20+20+0 = 60
        self.assertEqual(ctx["today_volume"], _d(60))

        # Today profit = paid sales only (paid1+paid2 = 30; loss sale paid w/ sell=0 -> profit -5 each)
        # paid1=15, paid2=15, loss sale =0-5=-5 -> total 25.
        self.assertEqual(ctx["today_profit"], _d(25))

        # Today loss from zero (loss_snapshot for sell=0 is cost=5)
        self.assertEqual(ctx["today_loss_from_zero"], _d(5))

        # Total expenses
        self.assertEqual(ctx["total_expenses"], _d(100))

        # Net all time = profit - expenses = 25 - 100 = -75
        self.assertEqual(ctx["net_all_time"], _d(-75))

    def test_esim_sales_count_matches_today_only(self):
        """eSIM KPI must not include historical eSIM sales (same scope as today volume)."""
        old = timezone.now() - timedelta(days=1)
        s_old = self._make_sale(sell=20, paid=True, is_esim=True, ref="E-YEST")
        Sale.objects.filter(pk=s_old.pk).update(created_at=old)
        self._make_sale(sell=20, paid=True, is_esim=True, ref="E-TODAY")
        self._make_sale(sell=20, paid=True, is_esim=False, ref="NORM")
        r = self.client.get(reverse("reports:dashboard"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["esim_sales_count"], 1)


# ============================================================ Profit report
class ProfitReportTests(_ReportsBase):
    def test_empty_profit_report_renders(self):
        r = self.client.get(reverse("reports:profit_report"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["total_profit"] or 0, 0)
        self.assertEqual(list(r.context["by_company"]), [])

    def test_profit_report_aggregates_by_company(self):
        self._make_sale(sell=20, paid=True)
        self._make_sale(sell=20, paid=True)
        # PENDING shouldn't count toward profit
        self._make_sale(sell=20)
        r = self.client.get(reverse("reports:profit_report"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["total_profit"], _d(30))  # 2 * (20-5)
        rows = list(r.context["by_company"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["company__name"], "ReportCo")
        self.assertEqual(rows[0]["profit"], _d(30))
        self.assertEqual(rows[0]["cnt"], 2)

    def test_profit_report_date_filter_excludes_out_of_range(self):
        s = self._make_sale(sell=20, paid=True)
        # Backdate the sale to 30 days ago by manipulating created_at
        old = timezone.now() - timedelta(days=30)
        Sale.objects.filter(pk=s.pk).update(created_at=old)
        today = timezone.localdate()
        r = self.client.get(
            reverse("reports:profit_report"),
            {"date_from": today.isoformat(), "date_to": today.isoformat()},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["total_profit"] or 0, 0)


# ============================================================ Sales report
class SalesReportTests(_ReportsBase):
    def test_empty_sales_report_renders(self):
        r = self.client.get(reverse("reports:sales_report"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["page_obj"].paginator.count, 0)

    def test_sales_report_filters_by_query(self):
        s1 = self._make_sale(payer="Alice")
        s2 = self._make_sale(payer="Bob")
        r = self.client.get(reverse("reports:sales_report"), {"q": "Alice"})
        self.assertEqual(r.status_code, 200)
        results = list(r.context["page_obj"].object_list)
        self.assertIn(s1, results)
        self.assertNotIn(s2, results)

    def test_htmx_request_returns_partial_template(self):
        self._make_sale()
        r = self.client.get(
            reverse("reports:sales_report"), HTTP_HX_REQUEST="true"
        )
        self.assertEqual(r.status_code, 200)
        templates = [t.name for t in r.templates if t.name]
        self.assertIn("reports/partials/sales_report_results.html", templates)
        self.assertNotIn("reports/sales_report.html", templates)

    def test_results_container_is_hx_boosted(self):
        """The whole results container is hx-boost'd so sort headers and
        pagination links update the table in place instead of triggering
        a full page reload (which was visibly jarring + lost scroll
        position on every column-sort click)."""
        r = self.client.get(reverse("reports:sales_report"))
        self.assertContains(r, 'id="sales-report-results"')
        self.assertContains(r, 'hx-boost="true"')
        self.assertContains(r, 'hx-target="#sales-report-results"')


# ============================================================ Employee report
class EmployeeReportTests(_ReportsBase):
    def test_empty_employee_report_renders(self):
        r = self.client.get(reverse("reports:employee_report"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["summary"]["sales_count"], 0)
        self.assertEqual(r.context["rows"], [])

    def test_employee_report_aggregates_per_user(self):
        other = User.objects.create_user("staff2", password="x")
        UserProfile.objects.update_or_create(
            user=other,
            defaults={"role": UserProfile.Role.EMPLOYEE, "is_active_profile": True},
        )
        self._make_sale(sell=20, paid=True)
        self._make_sale(sell=20, paid=True)
        s = create_sale(
            company=self.company,
            product=self.product,
            reference_number="X-1",
            payer_name="A",
            payment_method=self.cash,
            sell_price_actual=_d(50),
            notes="",
            user=other,
        )
        mark_sale_paid(sale=s, user=other)
        r = self.client.get(reverse("reports:employee_report"))
        self.assertEqual(r.status_code, 200)
        rows = {row["username"]: row for row in r.context["rows"]}
        self.assertEqual(rows["mgr_rep"]["sales_count"], 2)
        self.assertEqual(rows["mgr_rep"]["volume"], _d(40))
        self.assertEqual(rows["staff2"]["sales_count"], 1)
        self.assertEqual(rows["staff2"]["volume"], _d(50))
        self.assertEqual(r.context["summary"]["active_staff"], 2)

    def test_date_filter_excludes_out_of_range(self):
        s = self._make_sale(sell=20, paid=True)
        old = timezone.now() - timedelta(days=120)
        Sale.objects.filter(pk=s.pk).update(created_at=old)
        today = timezone.localdate()
        r = self.client.get(
            reverse("reports:employee_report"),
            {"date_from": today.isoformat(), "date_to": today.isoformat()},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["summary"]["sales_count"], 0)


class DashboardChartsTests(_ReportsBase):
    def test_dashboard_exposes_chart_series(self):
        self._make_sale(sell=20, paid=True)
        r = self.client.get(reverse("reports:dashboard"))
        self.assertEqual(r.status_code, 200)
        ctx = r.context
        self.assertEqual(len(ctx["daily_series"]), ctx["chart_window_days"])
        self.assertEqual(ctx["daily_series"][-1]["count"], 1)
        self.assertEqual(ctx["daily_series"][-1]["volume"], 20.0)
        self.assertIn("day_letter", ctx["daily_series"][0])
        self.assertEqual(len(ctx["daily_series"][0]["day_letter"]), 1)
        self.assertEqual(ctx["chart_total_volume"], 20.0)
        self.assertAlmostEqual(
            ctx["chart_avg_volume"], 20.0 / ctx["chart_window_days"], places=6
        )
        self.assertGreaterEqual(len(ctx["top_companies"]), 1)
        self.assertEqual(ctx["top_companies"][0]["name"], "ReportCo")


# ============================================================ Company report
class CompanyReportTests(_ReportsBase):
    def test_empty_company_report_renders(self):
        r = self.client.get(reverse("companies:company_detail", args=[self.company.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["company"], self.company)
        self.assertEqual(r.context["sales_page"].paginator.count, 0)

    def test_legacy_company_report_url_redirects(self):
        r = self.client.get(reverse("reports:company_report", args=[self.company.pk]))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(
            r.url,
            reverse("companies:company_detail", args=[self.company.pk]),
        )

    def test_tabs_and_hx_boost(self):
        r = self.client.get(reverse("companies:company_detail", args=[self.company.pk]))
        self.assertContains(r, "rd-section-tabs")
        self.assertContains(r, 'id="company-sales-grid"')
        self.assertContains(r, 'id="company-ledger-grid"')
        r_sales = self.client.get(
            reverse("companies:company_detail", args=[self.company.pk]),
            {"tab": "sales"},
        )
        self.assertContains(r_sales, 'hx-target="#company-sales-grid"')
        self.assertContains(r_sales, "Balance before")
        r_ledger = self.client.get(
            reverse("companies:company_detail", args=[self.company.pk]),
            {"tab": "ledger"},
        )
        self.assertContains(r_ledger, 'hx-target="#company-ledger-grid"')
        self.assertContains(r_ledger, "Phone / ref")

    def test_deposit_post_records_transaction(self):
        url = reverse("companies:company_detail", args=[self.company.pk])
        before = self.company.current_balance
        r = self.client.post(
            url,
            {"dep-amount": "100", "dep-notes": "test deposit", "dep-submit": "1"},
        )
        self.assertEqual(r.status_code, 302)
        self.assertIn("tab=general", r.url)
        self.company.refresh_from_db()
        self.assertEqual(self.company.current_balance, before + _d(100))

    def test_unknown_company_returns_404(self):
        r = self.client.get(reverse("companies:company_detail", args=[99999]))
        self.assertEqual(r.status_code, 404)

    def test_period_filter_scopes_aggregates_and_tables(self):
        from sales.services import record_manual_deposit

        # One sale today, one sale 60 days ago.
        recent = self._make_sale(sell=20, paid=True)
        old = self._make_sale(sell=20, paid=True)
        backdate = timezone.now() - timedelta(days=60)
        Sale.objects.filter(pk=old.pk).update(created_at=backdate)
        # Same for the matching DEDUCTION ledger row created when the
        # old sale was confirmed, otherwise a "this month" filter would
        # still include that historical consumption row.
        from sales.models import CompanyBalanceTransaction
        CompanyBalanceTransaction.objects.filter(
            company=self.company,
            entry_type=CompanyBalanceTransaction.EntryType.DEDUCTION,
            reference_id=old.pk,
        ).update(created_at=backdate)

        # A deposit today and a deposit 60 days ago.
        record_manual_deposit(
            company=self.company, amount=_d(500), notes="recent", user=self.user
        )
        old_dep = record_manual_deposit(
            company=self.company, amount=_d(900), notes="old", user=self.user
        )
        CompanyBalanceTransaction.objects.filter(pk=old_dep.pk).update(
            created_at=backdate
        )

        today = timezone.localdate()
        url = reverse("companies:company_detail", args=[self.company.pk])
        r = self.client.get(
            url,
            {
                "period_from": today.isoformat(),
                "period_to": today.isoformat(),
            },
        )
        self.assertEqual(r.status_code, 200)
        ctx = r.context
        self.assertTrue(ctx["period_active"])
        self.assertEqual(ctx["agg"]["cnt"], 1)
        self.assertEqual(ctx["agg"]["total_sell"], _d(20))
        self.assertEqual(ctx["deposits_total"], _d(500))
        self.assertEqual(ctx["consumed_total"], _d(5))
        self.assertIn(recent, ctx["sales_page"].object_list)
        self.assertNotIn(old, ctx["sales_page"].object_list)
        # closing balance uses the full ledger up to period_to (inclusive)
        # so it reflects history before the window as well.
        self.assertIsNotNone(ctx["balance_as_of"])

    def test_no_period_means_all_time(self):
        from sales.services import record_manual_deposit

        self._make_sale(sell=20, paid=True)
        record_manual_deposit(
            company=self.company, amount=_d(500), notes="x", user=self.user
        )
        r = self.client.get(reverse("companies:company_detail", args=[self.company.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.context["period_active"])
        self.assertIsNone(r.context["balance_as_of"])
        self.assertEqual(r.context["agg"]["cnt"], 1)
        self.assertEqual(r.context["deposits_total"], _d(500))


class StalePhonesReportTests(_ReportsBase):
    """Disconnect list uses: last sale + duration_days + threshold."""

    def _age_sale(self, sale, *, days_ago: int, duration_days: int = 30):
        Sale.objects.filter(pk=sale.pk).update(
            created_at=timezone.now() - timedelta(days=days_ago),
            duration_days=duration_days,
        )

    def test_stale_list_uses_duration_plus_threshold(self):
        # 60-day charge + 90 threshold → due after 150 days.
        s = self._make_sale(ref="0555544071", paid=True)
        self._age_sale(s, days_ago=150, duration_days=60)
        r = self.client.get(reverse("reports:stale_phones_report"))
        self.assertEqual(r.status_code, 200)
        rows = list(r.context["page_obj"].object_list)
        hit = next(x for x in rows if x["ref_key"] == "0555544071")
        self.assertEqual(hit["duration_days"], 60)
        self.assertGreaterEqual(hit["days_past_end"], 90)

    def test_not_due_until_threshold_after_subscription_end(self):
        # 60 + 90 = 150; at 140 days still hidden.
        s = self._make_sale(ref="0559990000", paid=True)
        self._age_sale(s, days_ago=140, duration_days=60)
        r = self.client.get(reverse("reports:stale_phones_report"))
        rows = list(r.context["page_obj"].object_list)
        self.assertFalse(any(x["ref_key"] == "0559990000" for x in rows))

    def test_recent_sale_not_in_stale_list(self):
        s = self._make_sale(ref="0559990001", paid=True)
        self._age_sale(s, days_ago=1, duration_days=30)
        r = self.client.get(reverse("reports:stale_phones_report"))
        rows = list(r.context["page_obj"].object_list)
        self.assertFalse(any(x["ref_key"] == "0559990001" for x in rows))

    def test_esim_latest_sale_excluded_from_disconnect_list(self):
        s = self._make_sale(ref="055ESIM0001", paid=True, is_esim=True)
        self._age_sale(s, days_ago=200, duration_days=30)
        r = self.client.get(reverse("reports:stale_phones_report"))
        rows = list(r.context["page_obj"].object_list)
        self.assertFalse(any(x["ref_key"] == "055esim0001" for x in rows))

    def test_physical_sale_after_esim_can_appear(self):
        """Older eSIM history does not block a later physical charge."""
        old_esim = self._make_sale(ref="055ESIM0002", paid=True, is_esim=True)
        self._age_sale(old_esim, days_ago=250, duration_days=30)
        physical = self._make_sale(ref="055ESIM0002", paid=True, is_esim=False)
        self._age_sale(physical, days_ago=130, duration_days=30)
        r = self.client.get(reverse("reports:stale_phones_report"))
        rows = list(r.context["page_obj"].object_list)
        self.assertTrue(any(x["ref_key"] == "055esim0002" for x in rows))

    def test_new_charge_resets_timer_and_hides_from_list(self):
        """A fresh recharge clears the number until the new period + threshold elapse."""
        s1 = self._make_sale(ref="055RESET001", paid=True)
        self._age_sale(s1, days_ago=200, duration_days=30)
        r1 = self.client.get(reverse("reports:stale_phones_report"))
        self.assertTrue(
            any(x["ref_key"] == "055reset001" for x in r1.context["page_obj"].object_list)
        )
        s2 = self._make_sale(ref="055RESET001", paid=True)
        self._age_sale(s2, days_ago=1, duration_days=60)
        r2 = self.client.get(reverse("reports:stale_phones_report"))
        self.assertFalse(
            any(x["ref_key"] == "055reset001" for x in r2.context["page_obj"].object_list)
        )
        # After the new 60 + 90 window, it reappears with the new duration.
        self._age_sale(s2, days_ago=160, duration_days=60)
        r3 = self.client.get(reverse("reports:stale_phones_report"))
        hit = next(
            x for x in r3.context["page_obj"].object_list if x["ref_key"] == "055reset001"
        )
        self.assertEqual(hit["duration_days"], 60)
        self.assertGreaterEqual(hit["days_past_end"], 90)

    def test_cancelled_sale_ignored_for_last_activity(self):
        s = self._make_sale(ref="0551112222", paid=True)
        self._age_sale(s, days_ago=5, duration_days=30)
        s2 = self._make_sale(ref="0551112222", paid=True)
        Sale.objects.filter(pk=s2.pk).update(
            created_at=timezone.now() - timedelta(days=200),
            duration_days=30,
            status=Sale.Status.CANCELLED,
        )
        r = self.client.get(reverse("reports:stale_phones_report"))
        rows = list(r.context["page_obj"].object_list)
        self.assertFalse(any(x["ref_key"] == "0551112222" for x in rows))

    def test_save_threshold_updates_profile(self):
        r = self.client.post(
            reverse("reports:stale_phones_report"),
            {
                "save_stale_threshold": "1",
                "stale_phone_threshold_days": "30",
            },
        )
        self.assertEqual(r.status_code, 302)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.stale_phone_threshold_days, 30)

    def test_dismiss_preserves_list_query(self):
        s = self._make_sale(ref="0550000001", paid=True)
        self._age_sale(s, days_ago=130, duration_days=30)
        r = self.client.post(
            reverse("reports:stale_phone_dismiss", kwargs={"ref_key": "0550000001"}),
            {"next_query": "q=055&sort=ref&order=asc"},
        )
        self.assertEqual(r.status_code, 302)
        self.assertIn("q=055", r["Location"])
        self.assertIn("sort=ref", r["Location"])
        r2 = self.client.get(reverse("reports:stale_phones_report"))
        rows = list(r2.context["page_obj"].object_list)
        self.assertFalse(any(x["ref_key"] == "0550000001" for x in rows))

    def test_new_sale_after_dismiss_allows_relist_when_idle_again(self):
        s1 = self._make_sale(ref="0550000003", paid=True)
        self._age_sale(s1, days_ago=130, duration_days=30)
        self.client.post(
            reverse("reports:stale_phone_dismiss", kwargs={"ref_key": "0550000003"}),
        )
        s2 = self._make_sale(ref="0550000003", paid=True)
        self.assertFalse(
            PhoneLineTracking.objects.filter(
                reference_key="0550000003", is_dismissed=True
            ).exists()
        )
        self._age_sale(s2, days_ago=130, duration_days=30)
        r = self.client.get(reverse("reports:stale_phones_report"))
        rows = list(r.context["page_obj"].object_list)
        self.assertTrue(any(x["ref_key"] == "0550000003" for x in rows))

    def test_inline_sim_save_persists(self):
        s = self._make_sale(ref="0550000002", paid=True)
        self._age_sale(s, days_ago=130, duration_days=30)
        url = reverse("reports:stale_phone_sim_save", kwargs={"ref_key": "0550000002"})
        r = self.client.post(
            url,
            {"sim_identifier": "SIM-ABC-99", "next_query": "sort=ref"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(r.status_code, 200)
        row = PhoneLineTracking.objects.get(reference_key="0550000002")
        self.assertEqual(row.sim_identifier, "SIM-ABC-99")
        self.assertIn("SIM-ABC-99", r.content.decode())

    def test_edit_sim_returns_to_list_query(self):
        s = self._make_sale(ref="0550000005", paid=True)
        self._age_sale(s, days_ago=130, duration_days=30)
        url = reverse("reports:stale_phone_edit", kwargs={"ref_key": "0550000005"})
        r = self.client.post(
            url,
            {"sim_identifier": "ICCID-1", "next_query": "company=&line_name=l&page=1"},
        )
        self.assertEqual(r.status_code, 302)
        self.assertIn("line_name=l", r["Location"])

    def test_default_sort_most_days_past_end_first(self):
        s_old = self._make_sale(ref="0557000001", paid=True)
        self._age_sale(s_old, days_ago=200, duration_days=30)
        s_new = self._make_sale(ref="0557000002", paid=True)
        self._age_sale(s_new, days_ago=130, duration_days=30)
        r = self.client.get(reverse("reports:stale_phones_report"))
        rows = list(r.context["page_obj"].object_list)
        idx_old = next(i for i, x in enumerate(rows) if x["ref_key"] == "0557000001")
        idx_new = next(i for i, x in enumerate(rows) if x["ref_key"] == "0557000002")
        self.assertLess(idx_old, idx_new)
        self.assertGreater(rows[idx_old]["days_past_end"], rows[idx_new]["days_past_end"])

    def test_row_shows_payer_company_line(self):
        s = self._make_sale(ref="0557000003", paid=True, payer="Karim")
        self._age_sale(s, days_ago=130, duration_days=30)
        r = self.client.get(reverse("reports:stale_phones_report"))
        row = next(x for x in r.context["page_obj"].object_list if x["ref_key"] == "0557000003")
        self.assertEqual(row["customer_or_payer"], "Karim")
        self.assertEqual(row["company_name"], "ReportCo")
        self.assertEqual(row["line_name"], self.line.name)

    def test_row_prefers_customer_name_over_payer(self):
        cust = create_customer(name="Ziad Ledger", user=self.user)
        s = self._make_sale(ref="0557000004", on_account=True, customer=cust, payer="Walk-in")
        self._age_sale(s, days_ago=130, duration_days=30)
        r = self.client.get(reverse("reports:stale_phones_report"))
        row = next(x for x in r.context["page_obj"].object_list if x["ref_key"] == "0557000004")
        self.assertEqual(row["customer_or_payer"], "Ziad Ledger")

    def test_sort_querystring_ref_asc(self):
        s1 = self._make_sale(ref="0557000009", paid=True)
        s2 = self._make_sale(ref="0557000008", paid=True)
        self._age_sale(s1, days_ago=130, duration_days=30)
        self._age_sale(s2, days_ago=130, duration_days=30)
        r = self.client.get(
            reverse("reports:stale_phones_report"),
            {"sort": "ref", "order": "asc"},
        )
        self.assertEqual(r.context["sort"], "ref")
        self.assertEqual(r.context["order"], "asc")
        refs = [
            x["ref_key"]
            for x in r.context["page_obj"].object_list
            if x["ref_key"].startswith("055700000")
        ]
        self.assertEqual(refs, sorted(refs))

    def test_filter_company_limits_rows(self):
        s1 = self._make_sale(ref="0558000001", paid=True)
        self._age_sale(s1, days_ago=130, duration_days=30)
        company2 = Company.objects.create(
            name="FilterCo",
            opening_balance=_d(5000),
            current_balance=_d(5000),
        )
        line2 = ProductLine.objects.create(company=company2, name="L2")
        product2 = Product.objects.create(
            line=line2,
            variant_label="V",
            cost_price=_d(2),
            default_sell_price=_d(12),
        )
        s2 = create_sale(
            company=company2,
            product=product2,
            reference_number="0558000002",
            payer_name="Other",
            payment_method=self.cash,
            sell_price_actual=_d(12),
            notes="",
            user=self.user,
            duration_days=30,
        )
        mark_sale_paid(sale=s2, user=self.user)
        Sale.objects.filter(pk=s2.pk).update(created_at=timezone.now() - timedelta(days=130))
        r = self.client.get(
            reverse("reports:stale_phones_report"),
            {"company": str(company2.pk)},
        )
        rows = list(r.context["page_obj"].object_list)
        self.assertEqual([x["ref_key"] for x in rows], ["0558000002"])

    def test_filter_line_name_unifies_across_companies(self):
        self.line.name = "Silicom"
        self.line.save(update_fields=["name"])
        s1 = self._make_sale(ref="0558100001", paid=True)
        self._age_sale(s1, days_ago=130, duration_days=30)
        company2 = Company.objects.create(
            name="SkyCo",
            opening_balance=_d(5000),
            current_balance=_d(5000),
        )
        line2 = ProductLine.objects.create(company=company2, name="Silicom")
        product2 = Product.objects.create(
            line=line2,
            variant_label="100GB",
            cost_price=_d(2),
            default_sell_price=_d(12),
        )
        s2 = create_sale(
            company=company2,
            product=product2,
            reference_number="0558100002",
            payer_name="Other",
            payment_method=self.cash,
            sell_price_actual=_d(12),
            notes="",
            user=self.user,
            duration_days=30,
        )
        mark_sale_paid(sale=s2, user=self.user)
        Sale.objects.filter(pk=s2.pk).update(created_at=timezone.now() - timedelta(days=130))
        # Different line name should not match.
        other = ProductLine.objects.create(company=company2, name="OtherLine")
        p_other = Product.objects.create(
            line=other,
            variant_label="X",
            cost_price=_d(2),
            default_sell_price=_d(12),
        )
        s3 = create_sale(
            company=company2,
            product=p_other,
            reference_number="0558100003",
            payer_name="Z",
            payment_method=self.cash,
            sell_price_actual=_d(12),
            notes="",
            user=self.user,
            duration_days=30,
        )
        mark_sale_paid(sale=s3, user=self.user)
        Sale.objects.filter(pk=s3.pk).update(created_at=timezone.now() - timedelta(days=130))

        r = self.client.get(
            reverse("reports:stale_phones_report"),
            {"line_name": "silicom"},
        )
        refs = {x["ref_key"] for x in r.context["page_obj"].object_list}
        self.assertIn("0558100001", refs)
        self.assertIn("0558100002", refs)
        self.assertNotIn("0558100003", refs)

    def test_filter_search_q_matches_payer(self):
        s = self._make_sale(ref="0559000111", paid=True, payer="UniquePayerXYZ")
        self._age_sale(s, days_ago=130, duration_days=30)
        r = self.client.get(reverse("reports:stale_phones_report"), {"q": "UniquePayerXYZ"})
        rows = list(r.context["page_obj"].object_list)
        self.assertTrue(any(x["ref_key"] == "0559000111" for x in rows))

    def test_htmx_returns_results_partial_only(self):
        s = self._make_sale(ref="0559000222", paid=True)
        self._age_sale(s, days_ago=130, duration_days=30)
        r = self.client.get(
            reverse("reports:stale_phones_report"),
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn("table", body)
        self.assertNotIn("Activity threshold", body)
