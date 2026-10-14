"""Fail-soft Redis cache: a Redis outage must not break writes or pages."""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

# Nothing listens on port 1: every call fails fast with a connection error.
DOWN_REDIS_CACHES = {
    "default": {
        "BACKEND": "core.cache.FailSoftRedisCache",
        "LOCATION": "redis://127.0.0.1:1/0",
        "OPTIONS": {"socket_connect_timeout": 0.5, "socket_timeout": 0.5},
    }
}


@override_settings(CACHES=DOWN_REDIS_CACHES)
class FailSoftRedisCacheTests(TestCase):
    def setUp(self):
        cache.close_circuit()

    def test_failure_skips_redis_for_a_while_and_logs_once(self):
        from unittest.mock import patch

        from django.core.cache.backends.redis import RedisCache

        with self.assertLogs("core.cache", level="WARNING") as logs:
            self.assertIsNone(cache.get("k"))
        self.assertEqual(len(logs.output), 1)

        with patch.object(RedisCache, "get") as redis_get, patch.object(
            RedisCache, "incr"
        ) as redis_incr:
            self.assertEqual(cache.get("k", "dflt"), "dflt")
            with self.assertRaises(ValueError):
                cache.incr("k")
        redis_get.assert_not_called()
        redis_incr.assert_not_called()

        cache.close_circuit()
        with patch.object(RedisCache, "get", return_value="up") as redis_get:
            self.assertEqual(cache.get("k"), "up")
        redis_get.assert_called_once()

    def test_errors_are_misses_and_noops(self):
        with self.assertLogs("core.cache", level="WARNING"):
            self.assertIsNone(cache.get("k"))
            self.assertEqual(cache.get("k", "dflt"), "dflt")
            self.assertEqual(cache.get_many(["a", "b"]), {})
            cache.set("k", 1)
            self.assertFalse(cache.add("k", 1))
            cache.delete("k")
            cache.set_many({"a": 1})
            cache.delete_many(["a"])
            self.assertFalse(cache.has_key("k"))
            self.assertFalse(cache.touch("k"))
            cache.clear()
            with self.assertRaises(ValueError):
                cache.incr("k")
            with self.assertRaises(ValueError):
                cache.decr("k")
            self.assertEqual(cache.get_or_set("k", 5), 5)

    def test_version_bumps_survive_an_outage(self):
        from core.context_processors import bump_nav_notifications_version
        from core.kpi_cache import bump_kpi_version, cached_kpi

        with self.assertLogs("core.cache", level="WARNING"):
            bump_kpi_version()
            bump_nav_notifications_version()
            self.assertEqual(cached_kpi("x", lambda: 7), 7)

    def test_create_sale_succeeds_with_redis_down(self):
        from django.contrib.auth import get_user_model

        from companies.models import Company, Product, ProductLine
        from sales.models import PaymentMethod, Sale
        from sales.services import create_sale

        user = get_user_model().objects.create_user("emp", password="x")
        company = Company.objects.create(
            name="Co", opening_balance=Decimal("1000"), current_balance=Decimal("1000")
        )
        line = ProductLine.objects.create(company=company, name="L")
        product = Product.objects.create(
            line=line,
            variant_label="P",
            cost_price=Decimal("5"),
            default_sell_price=Decimal("10"),
        )
        payment_method = PaymentMethod.objects.create(name="Cash")
        with self.assertLogs("core.cache", level="WARNING"):
            sale = create_sale(
                company=company,
                product=product,
                reference_number="0599000001",
                payer_name="Walk-in",
                payment_method=payment_method,
                sell_price_actual=Decimal("10"),
                notes="",
                user=user,
            )
        self.assertTrue(Sale.objects.filter(pk=sale.pk).exists())

    def test_public_refresh_page_renders_with_redis_down(self):
        with self.assertLogs("core.cache", level="WARNING"):
            page = self.client.get(reverse("phone_refresh:public"))
        self.assertEqual(page.status_code, 200)


class RedisCacheSettingsTests(TestCase):
    def test_redis_cache_settings_are_fail_soft_with_short_timeouts(self):
        from config.settings.base import redis_cache_settings

        conf = redis_cache_settings("redis://redis:6379/0")
        self.assertEqual(conf["BACKEND"], "core.cache.FailSoftRedisCache")
        self.assertEqual(conf["LOCATION"], "redis://redis:6379/0")
        self.assertLessEqual(conf["OPTIONS"]["socket_connect_timeout"], 2)
        self.assertLessEqual(conf["OPTIONS"]["socket_timeout"], 2)
        self.assertNotIn("FailSoft", settings.CACHES["default"]["BACKEND"])
