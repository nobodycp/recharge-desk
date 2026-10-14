from __future__ import annotations

from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from companies.models import Company
from core.tests_cache import DOWN_REDIS_CACHES
from providers import health
from providers.models import ProviderConnection


def _company(name, provider_key):
    return Company.objects.create(name=name, provider_key=provider_key)


class HealthAlertsTests(TestCase):
    def setUp(self):
        cache.clear()
        health.reset_local_alerts()
        self.addCleanup(health.reset_local_alerts)
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

    def test_redis_down_gives_redis_and_worker_items_linking_to_first_company_connection_tab(self):
        first = _company("Zeta", "sky")
        _company("Alpha", "aloha")
        self.redis_mock.return_value = False

        items = health.alerts()

        url = reverse("companies:company_detail", args=[first.pk]) + "?tab=connection"
        self.assertEqual(
            items,
            [
                {"text": "Redis لا يستجيب", "url": url},
                {"text": "الـ worker متوقف أو الطلبات عالقة", "url": url},
            ],
        )
        self.worker_mock.assert_not_called()

    def test_redis_down_without_any_provider_company_has_empty_url(self):
        _company("NoSystem", "")
        self.redis_mock.return_value = False

        items = health.alerts()

        self.assertEqual(
            items,
            [
                {"text": "Redis لا يستجيب", "url": ""},
                {"text": "الـ worker متوقف أو الطلبات عالقة", "url": ""},
            ],
        )

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

    def test_failed_connection_without_company_alerts_with_key_and_no_url(self):
        ProviderConnection.objects.create(
            provider_key="sky", is_enabled=True, last_login_ok=False
        )
        self.assertEqual(
            health.alerts(), [{"text": "فشل تسجيل الدخول: sky", "url": ""}]
        )

    def test_result_is_cached_for_30_seconds(self):
        self.assertEqual(health.alerts(), [])
        self.redis_mock.return_value = False
        self.assertEqual(health.alerts(), [])  # served from cache
        self.assertEqual(self.redis_mock.call_count, 1)
        cache.delete(health.ALERTS_CACHE_KEY)
        self.assertEqual(len(health.alerts()), 2)  # Redis + worker items

    def test_cache_key_and_ttl(self):
        self.assertEqual(health.ALERTS_CACHE_KEY, "providers:health-alerts")
        self.assertEqual(health.ALERTS_CACHE_TTL, 30)


@override_settings(CACHES=DOWN_REDIS_CACHES)
class HealthAlertsRedisDownTests(TestCase):
    def setUp(self):
        cache.close_circuit()
        health.reset_local_alerts()
        self.addCleanup(health.reset_local_alerts)
        ProviderConnection.objects.all().delete()
        self.redis_patch = mock.patch.object(health, "_redis_ok", return_value=False)
        self.worker_patch = mock.patch.object(health, "_worker_ok", return_value=True)
        self.redis_mock = self.redis_patch.start()
        self.worker_mock = self.worker_patch.start()
        self.addCleanup(self.redis_patch.stop)
        self.addCleanup(self.worker_patch.stop)

    def test_does_not_raise_and_skips_worker_ping(self):
        items = health.alerts()

        self.assertEqual(
            [i["text"] for i in items],
            ["Redis لا يستجيب", "الـ worker متوقف أو الطلبات عالقة"],
        )
        self.worker_mock.assert_not_called()

    def test_process_local_fallback_avoids_reprobing_within_30_seconds(self):
        now = [1000.0]
        with mock.patch.object(health.time, "monotonic", side_effect=lambda: now[0]):
            first = health.alerts()
            now[0] += 29
            second = health.alerts()
            self.assertEqual(self.redis_mock.call_count, 1)
            self.assertEqual(second, first)
            now[0] += 2
            health.alerts()
        self.assertEqual(self.redis_mock.call_count, 2)
        self.worker_mock.assert_not_called()
