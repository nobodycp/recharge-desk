"""Tests for the dashboard KPI cache (core.kpi_cache + core.signals)."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from accounts.models import UserProfile
from companies.models import Company, Product, ProductLine
from core.kpi_cache import bump_kpi_version, cached_kpi, get_kpi_version
from sales.models import PaymentMethod, Sale
from sales.services import create_sale

User = get_user_model()


def _d(v):
    return Decimal(str(v))


class KpiCachePrimitivesTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_cached_kpi_runs_builder_only_once_within_a_version(self):
        calls = {"n": 0}

        def build():
            calls["n"] += 1
            return 42

        v = cached_kpi("test:scalar", build)
        self.assertEqual(v, 42)
        self.assertEqual(calls["n"], 1)

        v2 = cached_kpi("test:scalar", build)
        self.assertEqual(v2, 42)
        self.assertEqual(calls["n"], 1, "second call should hit the cache")

    def test_bump_invalidates_every_cached_kpi_at_once(self):
        cached_kpi("a", lambda: 1)
        cached_kpi("b", lambda: 2)

        before = get_kpi_version()
        bump_kpi_version()
        after = get_kpi_version()
        self.assertEqual(after, before + 1)

        # Both keys move to the new version, so the next read recomputes.
        calls = {"n": 0}

        def rebuild():
            calls["n"] += 1
            return 99

        cached_kpi("a", rebuild)
        cached_kpi("b", rebuild)
        self.assertEqual(calls["n"], 2)


class KpiCacheSignalsTests(TestCase):
    """Saves to tracked tables must bump the version automatically."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("kpi-user", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={
                "role": UserProfile.Role.MANAGEMENT,
                "is_active_profile": True,
            },
        )
        cls.company = Company.objects.create(
            name="KpiCo",
            opening_balance=_d(1000),
            current_balance=_d(1000),
        )
        cls.line = ProductLine.objects.create(company=cls.company, name="L")
        cls.product = Product.objects.create(
            line=cls.line,
            variant_label="P",
            cost_price=_d(2),
            default_sell_price=_d(10),
        )
        cls.cash = PaymentMethod.objects.create(name="Cash")

    def setUp(self):
        cache.clear()

    def _make_sale(self, ref):
        return create_sale(
            company=self.company,
            product=self.product,
            payment_method=self.cash,
            reference_number=ref,
            payer_name="QA",
            sell_price_actual=_d(10),
            notes="",
            user=self.user,
        )

    def test_creating_a_sale_bumps_the_version(self):
        before = get_kpi_version()
        self._make_sale("kpi-1")
        # create_sale also writes a CompanyBalanceTransaction, so the
        # version may bump more than once; the contract is "strictly
        # greater than before".
        self.assertGreater(get_kpi_version(), before)

    def test_deleting_a_sale_bumps_the_version(self):
        sale = self._make_sale("kpi-2")
        before = get_kpi_version()
        Sale.objects.filter(pk=sale.pk).delete()
        self.assertGreater(get_kpi_version(), before)

    def test_dashboard_reflects_new_sale_after_cache_warm(self):
        """End-to-end: load the dashboard (warms cache) → create a sale
        → reload → today's count must include the new sale."""
        self.client.force_login(self.user)
        # Warm the cache.
        first = self.client.get("/management/")
        self.assertEqual(first.status_code, 200)
        baseline = first.context["today_count"]

        # New sale → signal bumps version → next read recomputes.
        self._make_sale("kpi-3")

        second = self.client.get("/management/")
        self.assertEqual(second.context["today_count"], baseline + 1)
