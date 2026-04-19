"""Tests for the reports app: dashboard KPIs and report views."""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserProfile
from companies.models import Company, Product, ProductLine
from customers.models import Customer
from customers.services import create_customer
from expenses.models import Expense
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
        self.client = Client()
        self.client.force_login(self.user)

    def _make_sale(self, *, sell=20, paid=False, on_account=False, customer=None, ref=None, payer="Ali"):
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


# ============================================================ Company report
class CompanyReportTests(_ReportsBase):
    def test_empty_company_report_renders(self):
        r = self.client.get(reverse("reports:company_report", args=[self.company.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["company"], self.company)
        self.assertEqual(r.context["sales_page"].paginator.count, 0)

    def test_deposit_post_records_transaction(self):
        url = reverse("reports:company_report", args=[self.company.pk])
        before = self.company.current_balance
        r = self.client.post(
            url,
            {"dep-amount": "100", "dep-notes": "test deposit", "dep-submit": "1"},
        )
        self.assertEqual(r.status_code, 302)
        self.company.refresh_from_db()
        self.assertEqual(self.company.current_balance, before + _d(100))

    def test_unknown_company_returns_404(self):
        r = self.client.get(reverse("reports:company_report", args=[99999]))
        self.assertEqual(r.status_code, 404)
