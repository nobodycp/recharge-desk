from __future__ import annotations

from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from companies.models import Company
from providers import health
from providers.models import ProviderConnection


def _company(name, provider_key):
    return Company.objects.create(name=name, provider_key=provider_key)


class HealthAlertsTests(TestCase):
    def setUp(self):
        cache.clear()
        ProviderConnection.objects.all().delete()
        patches = [
            mock.patch.object(health, "_redis_ok", return_value=True),
            mock.patch.object(health, "_worker_ok", return_value=True),
        ]
        self.redis_mock, self.worker_mock = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)

    def test_healthy_returns_empty_list(self):
        self.assertEqual(health.alerts(), [])

    def test_redis_down_gives_single_item_linking_to_first_company_connection_tab(self):
        first = _company("Zeta", "sky")
        _company("Alpha", "aloha")
        self.redis_mock.return_value = False

        items = health.alerts()

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["text"], "Redis لا يستجيب")
        self.assertEqual(
            items[0]["url"],
            reverse("companies:company_detail", args=[first.pk]) + "?tab=connection",
        )

    def test_redis_down_without_any_provider_company_has_empty_url(self):
        _company("NoSystem", "")
        self.redis_mock.return_value = False

        items = health.alerts()

        self.assertEqual(items, [{"text": "Redis لا يستجيب", "url": ""}])

    def test_worker_down_gives_worker_item(self):
        _company("Co", "sky")
        self.worker_mock.return_value = False

        items = health.alerts()

        self.assertEqual([i["text"] for i in items], ["الـ worker متوقف أو الطلبات عالقة"])

    def test_stalled_queue_gives_worker_item_once(self):
        self.worker_mock.return_value = False
        with mock.patch.object(health, "refresh_queue_stalled", return_value=True):
            items = health.alerts()
        self.assertEqual([i["text"] for i in items], ["الـ worker متوقف أو الطلبات عالقة"])

    def test_stalled_queue_with_live_worker_gives_worker_item(self):
        with mock.patch.object(health, "refresh_queue_stalled", return_value=True):
            items = health.alerts()
        self.assertEqual([i["text"] for i in items], ["الـ worker متوقف أو الطلبات عالقة"])

    def test_enabled_connection_with_failed_login_alerts_with_company_name(self):
        company = _company("شركة سكاي", "sky")
        ProviderConnection.objects.create(
            provider_key="sky", is_enabled=True, last_login_ok=False
        )

        items = health.alerts()

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["text"], "فشل تسجيل الدخول: شركة سكاي")
        self.assertTrue(items[0]["url"].endswith("?tab=connection"))
        self.assertEqual(
            items[0]["url"],
            reverse("companies:company_detail", args=[company.pk]) + "?tab=connection",
        )

    def test_failed_login_uses_first_company_with_that_key(self):
        first = _company("First", "sky")
        _company("Second", "sky")
        ProviderConnection.objects.create(
            provider_key="sky", is_enabled=True, last_login_ok=False
        )

        items = health.alerts()

        self.assertEqual(items[0]["text"], "فشل تسجيل الدخول: First")
        self.assertIn(f"/{first.pk}/", items[0]["url"])

    def test_disabled_connection_with_failed_login_has_no_alert(self):
        _company("Co", "sky")
        ProviderConnection.objects.create(
            provider_key="sky", is_enabled=False, last_login_ok=False
        )
        self.assertEqual(health.alerts(), [])

    def test_never_tried_or_ok_connection_has_no_alert(self):
        _company("Co", "sky")
        _company("Co2", "aloha")
        ProviderConnection.objects.create(
            provider_key="sky", is_enabled=True, last_login_ok=None
        )
        ProviderConnection.objects.create(
            provider_key="aloha", is_enabled=True, last_login_ok=True
        )
        self.assertEqual(health.alerts(), [])

    def test_failed_connection_without_company_is_skipped(self):
        ProviderConnection.objects.create(
            provider_key="sky", is_enabled=True, last_login_ok=False
        )
        self.assertEqual(health.alerts(), [])

    def test_result_is_cached_for_30_seconds(self):
        self.assertEqual(health.alerts(), [])
        self.redis_mock.return_value = False
        self.assertEqual(health.alerts(), [])  # served from cache
        self.assertEqual(self.redis_mock.call_count, 1)
        cache.delete(health.ALERTS_CACHE_KEY)
        self.assertEqual(len(health.alerts()), 1)

    def test_cache_key_and_ttl(self):
        self.assertEqual(health.ALERTS_CACHE_KEY, "providers:health-alerts")
        self.assertEqual(health.ALERTS_CACHE_TTL, 30)
