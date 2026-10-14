"""Tests for the reports app: dashboard KPIs and report views."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone, translation

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
    def setUp(self):
        from unittest import mock

        from providers import health

        super().setUp()
        # Keep the dashboard's Redis/Celery probes independent of the environment.
        health.reset_local_alerts()
        self.addCleanup(health.reset_local_alerts)
        redis_patch = mock.patch.object(health, "_redis_ok", return_value=True)
        worker_patch = mock.patch.object(health, "_worker_ok", return_value=True)
        self.redis_ok = redis_patch.start()
        self.worker_ok = worker_patch.start()
        self.addCleanup(redis_patch.stop)
        self.addCleanup(worker_patch.stop)

    def test_empty_dashboard_renders(self):
        r = self.client.get(reverse("reports:dashboard"))
        self.assertEqual(r.status_code, 200)
        ctx = r.context
        self.assertEqual(ctx["pending_count"], 0)
        self.assertEqual(ctx["awaiting_count"], 0)
        self.assertEqual(ctx["customer_debt_total"], 0)
        self.assertEqual(ctx["period_count"], 0)
        self.assertEqual(ctx["period_volume"] or 0, 0)
        self.assertEqual(ctx["period_profit"] or 0, 0)
        self.assertEqual(ctx["period"], "day")
        self.assertEqual(ctx["period_esim_count"], 0)
        self.assertEqual(ctx["phones_active_count"], 0)
        self.assertEqual(ctx["phones_due_soon_count"], 0)
        self.assertEqual(ctx["phones_ended_under_threshold_count"], 0)
        self.assertEqual(ctx["phones_overdue_count"], 0)
        self.assertEqual(ctx["phones_esim_active_count"], 0)
        self.assertEqual(ctx["phones_lifecycle_total"], 0)
        self.assertNotContains(r, "Recent activity")
        self.assertNotContains(r, "rd-stat-icon--warning")
        self.assertContains(r, "Line status")
        self.assertContains(r, "rd-phone-donut")
        self.assertContains(r, "eSIM:")

    def _queued_refresh_job(self, age_seconds):
        from providers import jobs
        from providers.models import ProviderJob

        job = jobs.create_job(
            kind=ProviderJob.KIND_REFRESH, provider_key="sky", phone="0599000000", source="web"
        )
        ProviderJob.objects.filter(pk=job.pk).update(
            created_at=timezone.now() - timedelta(seconds=age_seconds)
        )
        return job

    def test_connection_health_block_hidden_when_queue_is_healthy(self):
        self._queued_refresh_job(60)
        stale_done = self._queued_refresh_job(600)
        type(stale_done).objects.filter(pk=stale_done.pk).update(status="done")
        r = self.client.get(reverse("reports:dashboard"))
        self.assertNotContains(r, 'id="connection-health-alert"')
        self.assertNotContains(r, "حالة الربط")

    def test_connection_health_block_shown_when_jobs_wait_over_two_minutes(self):
        self.company.provider_key = "sky"
        self.company.save(update_fields=["provider_key"])
        self._queued_refresh_job(180)
        r = self.client.get(reverse("reports:dashboard"))
        self.assertContains(r, 'id="connection-health-alert"')
        self.assertContains(r, "حالة الربط")
        self.assertContains(r, "الـ worker متوقف أو الطلبات عالقة")
        self.assertContains(
            r,
            reverse("companies:company_detail", args=[self.company.pk])
            + "?tab=connection",
        )
        self.assertNotContains(
            r, reverse("phone_refresh:providers_index") + "?tab=general"
        )

    def test_connection_health_block_renders_without_link_when_no_provider_company(self):
        self._queued_refresh_job(180)
        r = self.client.get(reverse("reports:dashboard"))
        self.assertContains(r, "الـ worker متوقف أو الطلبات عالقة")
        self.assertNotContains(r, "tab=connection")

    def test_connection_health_block_renders_each_alert_text(self):
        from unittest import mock

        items = [
            {"text": "Redis لا يستجيب", "url": "/x/?tab=connection"},
            {"text": "فشل تسجيل الدخول: شركة", "url": ""},
        ]
        with mock.patch("reports.views.health_alerts", return_value=items):
            r = self.client.get(reverse("reports:dashboard"))
        self.assertContains(r, "حالة الربط")
        self.assertContains(r, "Redis لا يستجيب")
        self.assertContains(r, 'href="/x/?tab=connection"')
        self.assertContains(r, "فشل تسجيل الدخول: شركة")

    def test_dashboard_does_not_ping_health_outside_cached_alerts(self):
        self.client.get(reverse("reports:dashboard"))
        self.client.get(reverse("reports:dashboard"))
        self.assertEqual(self.redis_ok.call_count, 1)
        self.assertEqual(self.worker_ok.call_count, 1)

    def test_refresh_queue_check_is_cached_briefly(self):
        from providers import health

        self.assertFalse(health.refresh_queue_stalled())
        self._queued_refresh_job(180)
        self.assertFalse(health.refresh_queue_stalled())
        cache.delete(health.REFRESH_QUEUE_ALERT_CACHE_KEY)
        self.assertTrue(health.refresh_queue_stalled())

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

    def test_donut_stops_are_valid_css_in_arabic(self):
        """Arabic formats 33.33 as "33,33", which breaks the conic-gradient."""
        from django.test import RequestFactory

        from reports.views import dashboard

        for i in range(3):
            s = self._make_sale(ref=f"055DONUT00{i}", paid=True)
            Sale.objects.filter(pk=s.pk).update(
                created_at=timezone.now() - timedelta(days=10 + 50 * i),
                duration_days=30,
            )
        request = RequestFactory().get("/management/")
        request.user = User.objects.get(pk=self.user.pk)
        request.session = self.client.session
        with translation.override("ar"):
            r = dashboard(request)
        self.assertEqual(r.status_code, 200)
        style = r.content.decode().split('style="--donut-active-end:', 1)[1].split('"', 1)[0]
        self.assertIn(".", style)
        self.assertNotIn(",", style)


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
        self.assertEqual(ctx["period_volume"], _d(60))

        # Today profit = paid sales only (paid1+paid2 = 30; loss sale paid w/ sell=0 -> profit -5 each)
        # paid1=15, paid2=15, loss sale =0-5=-5 -> total 25.
        self.assertEqual(ctx["period_profit"], _d(25))

        # Today loss from zero (loss_snapshot for sell=0 is cost=5)
        self.assertEqual(ctx["period_loss"], _d(5))

        # Today's expenses
        self.assertEqual(ctx["period_expenses"], _d(100))

        # Net = profit - expenses = 25 - 100 = -75
        self.assertEqual(ctx["period_net"], _d(-75))

    def test_esim_sales_count_matches_today_only(self):
        """eSIM KPI must not include historical eSIM sales (same scope as today volume)."""
        old = timezone.now() - timedelta(days=1)
        s_old = self._make_sale(sell=20, paid=True, is_esim=True, ref="E-YEST")
        Sale.objects.filter(pk=s_old.pk).update(created_at=old)
        self._make_sale(sell=20, paid=True, is_esim=True, ref="E-TODAY")
        self._make_sale(sell=20, paid=True, is_esim=False, ref="NORM")
        r = self.client.get(reverse("reports:dashboard"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["period_esim_count"], 1)


class DashboardPeriodTests(_ReportsBase):
    """The header switch scopes sales KPIs to today, the calendar month, or all time."""

    def setUp(self):
        super().setUp()
        today = timezone.localdate()
        self.month_start = today.replace(day=1)
        self._make_sale(sell=20, paid=True, ref="P-TODAY")
        # Earlier in the current calendar month (falls back to today on the 1st).
        earlier = self._make_sale(sell=20, paid=True, ref="P-MONTH")
        Sale.objects.filter(pk=earlier.pk).update(
            created_at=timezone.make_aware(datetime.combine(self.month_start, time(12)))
        )
        # Before this month but within 30 days when today is early in the month:
        # must be excluded from "month" (calendar month, not a rolling window).
        old = self._make_sale(sell=20, paid=True, ref="P-OLD")
        Sale.objects.filter(pk=old.pk).update(
            created_at=timezone.make_aware(
                datetime.combine(self.month_start - timedelta(days=1), time(12))
            )
        )
        Expense.objects.create(
            title="old", category="ops", amount=_d(7),
            date=self.month_start - timedelta(days=1), created_by=self.user,
        )

    def _get(self, period=None):
        params = {"period": period} if period else {}
        r = self.client.get(reverse("reports:dashboard"), params)
        self.assertEqual(r.status_code, 200)
        return r.context

    def test_day_is_default(self):
        ctx = self._get()
        self.assertEqual(ctx["period"], "day")
        today_only = 2 if self.month_start == timezone.localdate() else 1
        self.assertEqual(ctx["period_count"], today_only)
        self.assertEqual(len(ctx["chart_series"]), 14)

    def test_month_is_calendar_month(self):
        ctx = self._get("month")
        self.assertEqual(ctx["period_count"], 2)
        self.assertEqual(ctx["period_profit"], _d(30))
        self.assertEqual(ctx["period_expenses"], 0)
        self.assertEqual(ctx["chart_series"][0]["date"], self.month_start.isoformat())
        self.assertEqual(len(ctx["chart_series"]), timezone.localdate().day)

    def test_all_includes_everything(self):
        ctx = self._get("all")
        self.assertEqual(ctx["period_count"], 3)
        self.assertEqual(ctx["period_volume"], _d(60))
        self.assertEqual(ctx["period_expenses"], _d(7))
        self.assertEqual(ctx["period_net"], _d(45 - 7))
        self.assertEqual(ctx["chart_series"][-1]["count"], 2)
        self.assertEqual(sum(d["count"] for d in ctx["chart_series"]), 3)

    def test_choice_is_remembered_and_invalid_values_ignored(self):
        self._get("all")
        self.assertEqual(self._get()["period"], "all")
        self.assertEqual(self._get("week")["period"], "all")
        self.assertEqual(self._get("day")["period"], "day")

    def test_switch_renders_with_active_option(self):
        r = self.client.get(reverse("reports:dashboard"), {"period": "month"})
        self.assertContains(r, 'class="rd-period-switch__opt is-active" href="?period=month"')
        self.assertContains(r, 'href="?period=all"')


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
        self.assertEqual(len(ctx["chart_series"]), 14)
        self.assertEqual(ctx["chart_series"][-1]["count"], 1)
        self.assertEqual(ctx["chart_series"][-1]["volume"], 20.0)
        self.assertEqual(len(ctx["chart_series"][0]["tick"]), 1)
        self.assertEqual(ctx["chart_total_volume"], 20.0)
        self.assertAlmostEqual(ctx["chart_avg_volume"], 20.0 / 14, places=6)
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
            {
                "reference_number": "055STMTI001",
                "sim_identifier": "ICCID-STMT-1",
                "notes": "chip at shop",
                "next_query": "sort=ref",
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn("rdToast", r["HX-Trigger"])
        self.assertIn("Saved", r["HX-Trigger"])
        # Refreshes the whole results grid (not a single cell).
        self.assertContains(r, "rd-stale-phones-grid")
        self.assertContains(r, "ICCID-STMT-1")
        meta = PhoneLineTracking.objects.get(reference_key="055stmti001")
        self.assertEqual(meta.sim_identifier, "ICCID-STMT-1")
        self.assertEqual(meta.notes, "chip at shop")
        self.assertIsNone(meta.manual_reset_at)

    def test_inline_save_error_keeps_grid_and_toasts(self):
        s = self._make_sale(ref="055STMTE002", paid=True)
        self._age_sale(s, days_ago=5, duration_days=30)
        url = reverse("reports:stale_phone_sim_save", kwargs={"ref_key": "055STMTE002"})
        r = self.client.post(
            url,
            {
                "reference_number": "",
                "sim_identifier": "",
                "notes": "",
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["HX-Reswap"], "none")
        self.assertIn("rdToast", r["HX-Trigger"])
        self.assertIn("error", r["HX-Trigger"])

    def test_delete_removes_number_from_statement(self):
        s = self._make_sale(ref="055STMTD001", paid=True)
        self._age_sale(s, days_ago=5, duration_days=30)
        url = reverse("reports:phone_line_delete", kwargs={"ref_key": "055STMTD001"})
        r = self.client.post(url)
        self.assertEqual(r.status_code, 302)
        meta = PhoneLineTracking.objects.get(reference_key="055stmtd001")
        self.assertTrue(meta.is_dismissed)
        r2 = self.client.get(reverse("reports:phone_lines_report"))
        refs = {x["ref_key"] for x in r2.context["page_obj"].object_list}
        self.assertNotIn("055stmtd001", refs)
        # Sale history remains.
        self.assertTrue(Sale.objects.filter(pk=s.pk).exists())

    def test_legacy_dismissed_number_still_in_sales_is_searchable_after_clear(self):
        """Pre-#43 dismiss meant hide-from-stale-list; inventory must show them.

        Migration 0004 clears legacy flags; this locks the aggregate+filter path
        for a number that still has sales (e.g. 0535759763-style reports).
        """
        s = self._make_sale(ref="0535759763", paid=True)
        self._age_sale(s, days_ago=5, duration_days=30)
        PhoneLineTracking.objects.update_or_create(
            reference_key="0535759763",
            defaults={
                "is_dismissed": True,
                "last_display_reference": "0535759763",
            },
        )
        # Simulate post-migration clear (what 0004 does in production).
        PhoneLineTracking.objects.filter(is_dismissed=True).update(is_dismissed=False)
        r = self.client.get(
            reverse("reports:phone_lines_report"), {"q": "0535759763"}
        )
        refs = {x["ref_key"] for x in r.context["page_obj"].object_list}
        self.assertIn("0535759763", refs)

    def test_new_sale_restores_deleted_number_to_statement(self):
        s = self._make_sale(ref="055STMTRST1", paid=True)
        self._age_sale(s, days_ago=5, duration_days=30)
        self.client.post(
            reverse("reports:phone_line_delete", kwargs={"ref_key": "055STMTRST1"})
        )
        self.assertTrue(
            PhoneLineTracking.objects.get(reference_key="055stmtrst1").is_dismissed
        )
        # New sale clears dismiss via signal.
        self._make_sale(ref="055STMTRST1", paid=True)
        self.assertFalse(
            PhoneLineTracking.objects.get(reference_key="055stmtrst1").is_dismissed
        )
        r = self.client.get(
            reverse("reports:phone_lines_report"), {"q": "055STMTRST1"}
        )
        refs = {x["ref_key"] for x in r.context["page_obj"].object_list}
        self.assertIn("055stmtrst1", refs)

    def test_edit_number_renames_sales_and_resets_countdown(self):
        overdue = self._make_sale(ref="055STMTE010", paid=True)
        self._age_sale(overdue, days_ago=130, duration_days=30)
        # Inline save on the statement (no separate edit page).
        url = reverse("reports:stale_phone_sim_save", kwargs={"ref_key": "055STMTE010"})
        r = self.client.post(
            url,
            {
                "reference_number": "055STMTE099",
                "sim_identifier": "ICCID-X",
                "notes": "corrected",
                "next_query": "status=overdue",
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn("rdToast", r["HX-Trigger"])
        self.assertContains(r, "rd-stale-phones-grid")
        overdue.refresh_from_db()
        self.assertEqual(overdue.reference_number, "055STMTE099")
        self.assertFalse(
            PhoneLineTracking.objects.filter(reference_key="055stmte010").exists()
        )
        meta = PhoneLineTracking.objects.get(reference_key="055stmte099")
        self.assertEqual(meta.sim_identifier, "ICCID-X")
        self.assertEqual(meta.notes, "corrected")
        self.assertIsNotNone(meta.manual_reset_at)
        r2 = self.client.get(reverse("reports:phone_lines_report"))
        by_ref = {x["ref_key"]: x for x in r2.context["page_obj"].object_list}
        self.assertNotIn("055stmte010", by_ref)
        self.assertIn("055stmte099", by_ref)
        self.assertNotEqual(by_ref["055stmte099"]["status"], "overdue")
        self.assertTrue(by_ref["055stmte099"]["used_manual_renew"])

    def test_statement_shows_inline_number_iccid_notes_and_delete(self):
        s = self._make_sale(ref="055STMTU001", paid=True)
        self._age_sale(s, days_ago=5, duration_days=30)
        r = self.client.get(reverse("reports:phone_lines_report"))
        body = r.content.decode()
        self.assertIn('name="reference_number"', body)
        self.assertIn('name="notes"', body)
        self.assertIn('name="sim_identifier"', body)
        self.assertIn('id="stale-sim-form-055stmtu001"', body)
        # Save is inside the form and refreshes the results grid only.
        self.assertIn('hx-target="#phone-lines-list-results"', body)
        self.assertIn("rd-stale-phone-actions", body)
        self.assertNotIn(
            reverse("reports:stale_phone_edit", kwargs={"ref_key": "055stmtu001"}),
            body,
        )
        self.assertIn(
            reverse("reports:phone_line_delete", kwargs={"ref_key": "055stmtu001"}),
            body,
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

    def test_filters_use_boosted_page_nav_not_htmx_partial(self):
        """Filter Apply / sort / pagination are boosted page loads (whole body).

        The old hx-get → #phone-lines-list-results path left the filter form
        out of sync with the URL after Apply/Back, so filters looked broken.
        """
        s = self._make_sale(ref="055STMTL001", paid=True)
        self._age_sale(s, days_ago=5, duration_days=30)
        r = self.client.get(
            reverse("reports:phone_lines_report"), {"status": "active"}
        )
        body = r.content.decode()
        self.assertIn('id="phone-lines-filter-form"', body)
        self.assertNotIn('hx-get=', body)
        self.assertRegex(body, r'<body[^>]*hx-boost="true"')
        # Active filters keep the filter card open.
        self.assertIn('class="rd-card mb-3 position-relative rd-filter-card" open', body)

    def test_status_filter_survives_stale_line_name_choice(self):
        """A bad line_name must not wipe status / sim_type filters."""
        overdue = self._make_sale(ref="055STMTB001", paid=True)
        self._age_sale(overdue, days_ago=130, duration_days=30)
        active = self._make_sale(ref="055STMTB002", paid=True)
        self._age_sale(active, days_ago=5, duration_days=30)
        r = self.client.get(
            reverse("reports:phone_lines_report"),
            {"status": "overdue", "line_name": "not-a-real-line-xyz"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.context["filter_form"].is_valid())
        refs = {x["ref_key"] for x in r.context["page_obj"].object_list}
        self.assertIn("055stmtb001", refs)
        self.assertNotIn("055stmtb002", refs)

    def test_disconnect_report_removed(self):
        from django.urls import NoReverseMatch

        with self.assertRaises(NoReverseMatch):
            reverse("reports:stale_phones_report")

