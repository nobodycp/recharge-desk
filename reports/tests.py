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
        self.assertEqual(ctx["phones_active_count"], 0)
        self.assertEqual(ctx["phones_due_soon_count"], 0)
        self.assertEqual(ctx["phones_ended_under_threshold_count"], 0)
        self.assertEqual(ctx["phones_overdue_count"], 0)
        self.assertEqual(ctx["phones_esim_active_count"], 0)
        self.assertEqual(ctx["phones_lifecycle_total"], 0)
        self.assertNotContains(r, "Recent activity")
        self.assertContains(r, "Line status")
        self.assertContains(r, "rd-phone-donut")
        self.assertContains(r, "eSIM:")

    def test_dashboard_phone_disconnect_kpis(self):
        from reports.stale_phones import reference_disconnect_counts

        self.user.profile.stale_phone_threshold_days = 85
        self.user.profile.save(update_fields=["stale_phone_threshold_days"])

        # Overdue: duration 30 + threshold 85 → due after 115 days.
        overdue = self._make_sale(ref="055DASH0001", paid=True)
        Sale.objects.filter(pk=overdue.pk).update(
            created_at=timezone.now() - timedelta(days=130),
            duration_days=30,
        )
        # Due within 24h of subscription end: duration 30, age ~29.5 days
        # → charge ends in ~12 hours.
        due_soon = self._make_sale(ref="055DASH0002", paid=True)
        Sale.objects.filter(pk=due_soon.pk).update(
            created_at=timezone.now() - timedelta(days=29, hours=12),
            duration_days=30,
        )
        # Subscription ended, still deep in the grace window.
        grace = self._make_sale(ref="055DASH0005", paid=True)
        Sale.objects.filter(pk=grace.pk).update(
            created_at=timezone.now() - timedelta(days=60),
            duration_days=30,
        )
        # Still active (subscription running).
        fresh = self._make_sale(ref="055DASH0003", paid=True)
        Sale.objects.filter(pk=fresh.pk).update(
            created_at=timezone.now() - timedelta(days=10),
            duration_days=30,
        )
        # Active eSIM — included in Active total (= physical + eSIM).
        esim_active = self._make_sale(ref="055DASH0004", paid=True, is_esim=True)
        Sale.objects.filter(pk=esim_active.pk).update(
            created_at=timezone.now() - timedelta(days=5),
            duration_days=30,
        )
        # Ended eSIM — must not count under Active.
        esim_old = self._make_sale(ref="055DASH0006", paid=True, is_esim=True)
        Sale.objects.filter(pk=esim_old.pk).update(
            created_at=timezone.now() - timedelta(days=200),
            duration_days=30,
        )

        stats = reference_disconnect_counts(threshold_days=85, due_soon_hours=24)
        self.assertEqual(stats["overdue"], 1)
        self.assertEqual(stats["due_soon"], 1)
        self.assertEqual(stats["ended_under_threshold"], 1)
        self.assertEqual(stats["active"], 2)
        self.assertEqual(stats["esim_active"], 1)
        self.assertEqual(stats["total"], 5)

        cache.clear()
        r = self.client.get(reverse("reports:dashboard"))
        self.assertEqual(r.context["phones_overdue_count"], 1)
        self.assertEqual(r.context["phones_due_soon_count"], 1)
        self.assertEqual(r.context["phones_ended_under_threshold_count"], 1)
        self.assertEqual(r.context["phones_active_count"], 2)
        self.assertEqual(r.context["phones_esim_active_count"], 1)
        self.assertEqual(r.context["phones_lifecycle_total"], 5)
        self.assertContains(r, "Active lines")
        self.assertContains(r, "Ends today")
        self.assertContains(r, "Disconnected only")
        self.assertContains(r, "Left the agency")
        self.assertContains(r, "eSIM:")
        self.assertNotContains(r, "eSIM lines")
        self.assertNotContains(r, "Subscription still running")
        self.assertNotContains(r, "Last charge ends today")
        self.assertNotContains(r, "Open lines that left the agency")


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
        self.assertEqual(r.context["total_count"], 2)
        self.assertEqual(r.context["total_volume"], _d(40))
        self.assertEqual(r.context["max_company_profit"], _d(30))
        rows = list(r.context["by_company"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["company__name"], "ReportCo")
        self.assertEqual(rows[0]["profit"], _d(30))
        self.assertEqual(rows[0]["cnt"], 2)
        self.assertContains(r, "rd-profit-hero")
        self.assertContains(r, "rd-profit-rank")

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



class PhoneLinesStatementTests(_ReportsBase):
    """بيان الأرقام — primary phone inventory report."""

    def setUp(self):
        super().setUp()
        # Ensure profile uses the new default unless a test overrides it.
        self.user.profile.stale_phone_threshold_days = 85
        self.user.profile.save(update_fields=["stale_phone_threshold_days"])

    def _age_sale(self, sale, *, days_ago: int, duration_days: int = 30):
        Sale.objects.filter(pk=sale.pk).update(
            created_at=timezone.now() - timedelta(days=days_ago),
            duration_days=duration_days,
        )

    def test_statement_lists_active_and_overdue(self):
        # 30 + 85 = 115 → overdue after 115 days.
        overdue = self._make_sale(ref="055STMT0001", paid=True)
        self._age_sale(overdue, days_ago=130, duration_days=30)
        active = self._make_sale(ref="055STMT0002", paid=True)
        self._age_sale(active, days_ago=5, duration_days=30)
        r = self.client.get(reverse("reports:phone_lines_report"))
        self.assertEqual(r.status_code, 200)
        by_ref = {x["ref_key"]: x for x in r.context["page_obj"].object_list}
        self.assertEqual(by_ref["055stmt0001"]["status"], "overdue")
        self.assertEqual(by_ref["055stmt0002"]["status"], "active")
        self.assertContains(r, "Line statement")
        self.assertContains(r, "Days to leave the agency")
        self.assertNotContains(r, "Lines for disconnect")

    def test_statement_includes_esim(self):
        s = self._make_sale(ref="055STMTE001", paid=True, is_esim=True)
        self._age_sale(s, days_ago=5, duration_days=30)
        r = self.client.get(reverse("reports:phone_lines_report"))
        row = next(
            x for x in r.context["page_obj"].object_list if x["ref_key"] == "055stmte001"
        )
        self.assertTrue(row["is_esim"])

    def test_grace_not_overdue_until_threshold(self):
        # 60 + 85 = 145; at 140 days still under threshold.
        s = self._make_sale(ref="055STMTG001", paid=True)
        self._age_sale(s, days_ago=140, duration_days=60)
        r = self.client.get(reverse("reports:phone_lines_report"))
        row = next(
            x for x in r.context["page_obj"].object_list if x["ref_key"] == "055stmtg001"
        )
        self.assertEqual(row["status"], "ended_under_threshold")

    def test_filter_status_overdue_only(self):
        overdue = self._make_sale(ref="055STMTS001", paid=True)
        self._age_sale(overdue, days_ago=130, duration_days=30)
        active = self._make_sale(ref="055STMTS002", paid=True)
        self._age_sale(active, days_ago=5, duration_days=30)
        r = self.client.get(
            reverse("reports:phone_lines_report"), {"status": "overdue"}
        )
        refs = {x["ref_key"] for x in r.context["page_obj"].object_list}
        self.assertIn("055stmts001", refs)
        self.assertNotIn("055stmts002", refs)

    def test_threshold_in_filter_saves_to_profile(self):
        r = self.client.get(
            reverse("reports:phone_lines_report"),
            {"threshold_days": "40"},
        )
        self.assertEqual(r.status_code, 200)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.stale_phone_threshold_days, 40)
        self.assertEqual(r.context["threshold_days"], 40)

    def test_default_threshold_is_85(self):
        self.assertEqual(self.user.profile.stale_phone_threshold_days, 85)
        r = self.client.get(reverse("reports:phone_lines_report"))
        self.assertEqual(r.context["threshold_days"], 85)
        self.assertEqual(r.context["filter_form"]["threshold_days"].initial, 85)

    def test_renew_button_only_for_overdue(self):
        overdue = self._make_sale(ref="055STMTR001", paid=True)
        self._age_sale(overdue, days_ago=130, duration_days=30)
        active = self._make_sale(ref="055STMTR002", paid=True)
        self._age_sale(active, days_ago=5, duration_days=30)
        r = self.client.get(reverse("reports:phone_lines_report"))
        body = r.content.decode()
        renew_overdue = reverse(
            "reports:phone_line_renew", kwargs={"ref_key": "055stmtr001"}
        )
        renew_active = reverse(
            "reports:phone_line_renew", kwargs={"ref_key": "055stmtr002"}
        )
        self.assertIn(renew_overdue, body)
        self.assertNotIn(renew_active, body)

    def test_renew_sets_manual_reset_and_leaves_overdue(self):
        overdue = self._make_sale(ref="055STMTR003", paid=True)
        self._age_sale(overdue, days_ago=130, duration_days=30)
        url = reverse("reports:phone_line_renew", kwargs={"ref_key": "055STMTR003"})
        r = self.client.post(url, {"next_query": "status=overdue"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("phone-lines", r["Location"])
        meta = PhoneLineTracking.objects.get(reference_key="055stmtr003")
        self.assertIsNotNone(meta.manual_reset_at)
        r2 = self.client.get(reverse("reports:phone_lines_report"))
        row = next(
            x for x in r2.context["page_obj"].object_list if x["ref_key"] == "055stmtr003"
        )
        # 1-day renew ends within 24h → due_soon (day before charge end).
        self.assertEqual(row["status"], "due_soon")
        self.assertEqual(row["duration_days"], 1)
        self.assertTrue(row["used_manual_renew"])
        self.assertNotEqual(row["status"], "overdue")

    def test_renew_rejected_for_active_line(self):
        active = self._make_sale(ref="055STMTR004", paid=True)
        self._age_sale(active, days_ago=5, duration_days=30)
        url = reverse("reports:phone_line_renew", kwargs={"ref_key": "055STMTR004"})
        r = self.client.post(url)
        self.assertEqual(r.status_code, 404)

    def test_new_sale_clears_manual_renew(self):
        overdue = self._make_sale(ref="055STMTR005", paid=True)
        self._age_sale(overdue, days_ago=130, duration_days=30)
        self.client.post(
            reverse("reports:phone_line_renew", kwargs={"ref_key": "055STMTR005"})
        )
        self.assertIsNotNone(
            PhoneLineTracking.objects.get(reference_key="055stmtr005").manual_reset_at
        )
        self._make_sale(ref="055STMTR005", paid=True)
        self.assertIsNone(
            PhoneLineTracking.objects.get(reference_key="055stmtr005").manual_reset_at
        )

    def test_filter_sim_type_physical(self):
        phys = self._make_sale(ref="055STMTT001", paid=True, is_esim=False)
        self._age_sale(phys, days_ago=5, duration_days=30)
        esim = self._make_sale(ref="055STMTT002", paid=True, is_esim=True)
        self._age_sale(esim, days_ago=5, duration_days=30)
        r = self.client.get(
            reverse("reports:phone_lines_report"), {"sim_type": "physical"}
        )
        refs = {x["ref_key"] for x in r.context["page_obj"].object_list}
        self.assertIn("055stmtt001", refs)
        self.assertNotIn("055stmtt002", refs)

    def test_inline_sim_save_works_for_active_line(self):
        s = self._make_sale(ref="055STMTI001", paid=True)
        self._age_sale(s, days_ago=5, duration_days=30)
        url = reverse("reports:stale_phone_sim_save", kwargs={"ref_key": "055STMTI001"})
        r = self.client.post(
            url,
            {"sim_identifier": "ICCID-STMT-1", "next_query": "sort=ref"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            PhoneLineTracking.objects.get(reference_key="055stmti001").sim_identifier,
            "ICCID-STMT-1",
        )

    def test_htmx_returns_results_partial_only(self):
        s = self._make_sale(ref="055STMTH001", paid=True)
        self._age_sale(s, days_ago=5, duration_days=30)
        r = self.client.get(
            reverse("reports:phone_lines_report"),
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn("table", body)
        self.assertNotIn("<h1", body)

    def test_disconnect_report_removed(self):
        from django.urls import NoReverseMatch

        with self.assertRaises(NoReverseMatch):
            reverse("reports:stale_phones_report")

