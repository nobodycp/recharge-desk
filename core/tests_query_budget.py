"""Query-count budgets for the main pages.

Each page is fetched twice (the first GET warms caches), and the second GET
must stay within its pinned budget. A failure prints the count and the SQL so
an N+1 regression is easy to spot.
"""

from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.models import UserProfile
from companies.models import Company, Product, ProductLine
from customers.models import Customer
from sales.models import PaymentMethod
from sales.services import create_sale

User = get_user_model()

# Budgets are measured + 2: tighten them, never loosen without a reason.
BUDGETS = {
    "reports:dashboard": 7,
    "sales:management_sale_list": 13,
    "customers:customer_list": 11,
    "inventory:overview": 13,
    "sales:employee_entry": 8,
    "sales:employee_recent_sales": 12,
}


class QueryBudgetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
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
        for i in range(2):
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
            idx = i % 2
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
        # Keep the dashboard's Redis/Celery probes independent of the environment.
        health.reset_local_alerts()
        self.addCleanup(health.reset_local_alerts)
        for name in ("_redis_ok", "_worker_ok"):
            patcher = mock.patch.object(health, name, return_value=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def assert_max_queries(self, url_name, user, budget):
        url = reverse(url_name)
        client = Client()
        client.force_login(user)
        warm = client.get(url)
        self.assertEqual(warm.status_code, 200, f"{url_name} warm GET failed")
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(url)
        self.assertEqual(response.status_code, 200, f"{url_name} GET failed")
        count = len(ctx.captured_queries)
        sql = "\n".join(
            f"{n}. {q['sql']}" for n, q in enumerate(ctx.captured_queries, 1)
        )
        self.assertLessEqual(
            count,
            budget,
            f"{url_name} ran {count} queries (budget {budget}):\n{sql}",
        )

    def test_management_dashboard(self):
        self.assert_max_queries(
            "reports:dashboard", self.mgmt, BUDGETS["reports:dashboard"]
        )

    def test_management_sale_list(self):
        self.assert_max_queries(
            "sales:management_sale_list",
            self.mgmt,
            BUDGETS["sales:management_sale_list"],
        )

    def test_customer_list(self):
        self.assert_max_queries(
            "customers:customer_list", self.mgmt, BUDGETS["customers:customer_list"]
        )

    def test_inventory_overview(self):
        self.assert_max_queries(
            "inventory:overview", self.mgmt, BUDGETS["inventory:overview"]
        )

    def test_employee_entry(self):
        self.assert_max_queries(
            "sales:employee_entry", self.emp, BUDGETS["sales:employee_entry"]
        )

    def test_employee_recent_sales(self):
        self.assert_max_queries(
            "sales:employee_recent_sales",
            self.emp,
            BUDGETS["sales:employee_recent_sales"],
        )
