"""Query-count budgets for the main pages.

Each page is fetched twice (the first GET warms caches), and the second GET
must stay within its pinned budget. A failure prints the count and the SQL so
an N+1 regression is easy to spot.
"""

from datetime import datetime, time
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserProfile
from companies.models import Company, Product, ProductLine
from customers.models import Customer
from sales.models import PaymentMethod
from sales.services import create_sale

User = get_user_model()

COMPANY_COUNT = 8


def _fixed_now():
    """Local noon today: "today" and date-keyed caches cannot flip mid-test."""
    noon = datetime.combine(timezone.localdate(), time(12, 0))
    return timezone.make_aware(noon)


# Budgets are measured + 2: tighten them, never loosen without a reason.
BUDGETS = {
    "reports:dashboard": 7,
    "reports:dashboard (cold cache)": 35,
    "sales:management_sale_list": 13,
    "customers:customer_list": 11,
    # includes a known per-company N+1 (≈3 q/company); fix in the Suppliers & Stock phase, then tighten
    "inventory:overview": 31,
    "sales:employee_entry": 8,
    "sales:employee_recent_sales": 12,
}


class QueryBudgetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Pin "now" for fixture creation too (Sale.created_at is auto_now_add).
        cls.fixed_now = _fixed_now()
        patcher = mock.patch("django.utils.timezone.now", return_value=cls.fixed_now)
        patcher.start()
        try:
            cls._build_fixture()
        finally:
            patcher.stop()

    @classmethod
    def _build_fixture(cls):
        cls.mgmt = User.objects.create_user("qb_mgmt", password="x")
        UserProfile.objects.update_or_create(
            user=cls.mgmt,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        cls.emp = User.objects.create_user("qb_emp", password="x")
        UserProfile.objects.update_or_create(
            user=cls.emp,
            defaults={"role": UserProfile.Role.EMPLOYEE, "is_active_profile": True},
        )

        cls.companies = []
        cls.products = []
        for i in range(COMPANY_COUNT):
            company = Company.objects.create(
                name=f"QB Co {i}",
                opening_balance=Decimal("10000"),
                current_balance=Decimal("10000"),
            )
            line = ProductLine.objects.create(company=company, name=f"Line {i}")
            product = Product.objects.create(
                line=line,
                variant_label=f"Pkg {i}",
                cost_price=Decimal("5"),
                default_sell_price=Decimal("10"),
            )
            cls.companies.append(company)
            cls.products.append(product)

        cls.cash = PaymentMethod.objects.create(name="QB Cash")
        cls.customers = [
            Customer.objects.create(name=f"QB Customer {i}", created_by=cls.mgmt)
            for i in range(3)
        ]

        # 10 sales by the employee (so the recent-sales page has rows too);
        # a few are on account for customers.
        for i in range(10):
            idx = i % COMPANY_COUNT
            on_account = i % 3 == 0
            create_sale(
                company=cls.companies[idx],
                product=cls.products[idx],
                reference_number=f"059000{i:04d}",
                payer_name=f"Payer {i}",
                payment_method=None if on_account else cls.cash,
                sell_price_actual=Decimal("10"),
                notes="",
                user=cls.emp,
                on_account=on_account,
                customer=cls.customers[i % 3] if on_account else None,
            )

    def setUp(self):
        from providers import health

        cache.clear()
        self.addCleanup(cache.clear)
        now_patcher = mock.patch(
            "django.utils.timezone.now", return_value=self.fixed_now
        )
        now_patcher.start()
        self.addCleanup(now_patcher.stop)
        # Keep the dashboard's Redis/Celery probes independent of the environment.
        health.reset_local_alerts()
        self.addCleanup(health.reset_local_alerts)
        for name in ("_redis_ok", "_worker_ok"):
            patcher = mock.patch.object(health, name, return_value=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_fixture_time_is_pinned(self):
        from sales.models import Sale

        self.assertEqual(Sale.objects.count(), 10)
        self.assertFalse(Sale.objects.exclude(created_at=self.fixed_now).exists())
        self.assertEqual(timezone.now(), self.fixed_now)

    def assert_max_queries(self, url_name, user, budget, *, cold=False, label=None):
        """GET ``url_name`` and fail if it ran more than ``budget`` queries.

        Warm mode (default) does one throwaway GET first so caches are filled;
        cold mode clears the cache right before the single measured GET.
        Returns the measured response so callers can assert rendered content.
        """
        label = label or url_name
        url = reverse(url_name)
        client = Client()
        client.force_login(user)
        if cold:
            cache.clear()
        else:
            warm = client.get(url)
            self.assertEqual(warm.status_code, 200, f"{label} warm GET failed")
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(url)
        self.assertEqual(response.status_code, 200, f"{label} GET failed")
        count = len(ctx.captured_queries)
        sql = "\n".join(
            f"{n}. {q['sql']}" for n, q in enumerate(ctx.captured_queries, 1)
        )
        self.assertLessEqual(
            count,
            budget,
            f"{label} ran {count} queries (budget {budget}):\n{sql}",
        )
        return response

    def test_management_dashboard(self):
        self.assert_max_queries(
            "reports:dashboard", self.mgmt, BUDGETS["reports:dashboard"]
        )

    def test_management_dashboard_cold_cache(self):
        self.assert_max_queries(
            "reports:dashboard",
            self.mgmt,
            BUDGETS["reports:dashboard (cold cache)"],
            cold=True,
            label="reports:dashboard (cold cache)",
        )

    def test_management_sale_list(self):
        response = self.assert_max_queries(
            "sales:management_sale_list",
            self.mgmt,
            BUDGETS["sales:management_sale_list"],
        )
        self.assertContains(response, "0590000001")

    def test_customer_list(self):
        response = self.assert_max_queries(
            "customers:customer_list", self.mgmt, BUDGETS["customers:customer_list"]
        )
        self.assertContains(response, "QB Customer 0")

    def test_inventory_overview(self):
        response = self.assert_max_queries(
            "inventory:overview", self.mgmt, BUDGETS["inventory:overview"]
        )
        # Rows are product lines, one per company.
        self.assertContains(response, "Line 7")

    def test_employee_entry(self):
        response = self.assert_max_queries(
            "sales:employee_entry", self.emp, BUDGETS["sales:employee_entry"]
        )
        self.assertContains(response, "QB Co 7")

    def test_employee_recent_sales(self):
        response = self.assert_max_queries(
            "sales:employee_recent_sales",
            self.emp,
            BUDGETS["sales:employee_recent_sales"],
        )
        self.assertContains(response, "0590000001")
