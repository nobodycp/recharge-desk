"""The management shell (topbar + sidebar) renders the Pearl layout and keeps its JS hooks."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from accounts.models import UserProfile

User = get_user_model()


class ManagementShellTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("shell_mgr", password="x", first_name="Sami")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )

    def setUp(self):
        from providers import health

        cache.clear()
        health.reset_local_alerts()
        self.addCleanup(health.reset_local_alerts)
        for name in ("_redis_ok", "_worker_ok"):
            patcher = mock.patch.object(health, name, return_value=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client.force_login(self.user)

    def _dashboard(self):
        response = self.client.get(reverse("reports:dashboard"))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_topbar_greeting(self):
        html = self._dashboard()
        self.assertIn('class="rd-hello', html)
        self.assertIn("rd-hello__title", html)
        self.assertIn("rd-hello__date", html)
        self.assertIn("Sami", html)

    def test_topbar_keeps_controls_and_hooks(self):
        html = self._dashboard()
        self.assertIn("data-rd-search", html)
        self.assertIn("data-rd-search-toggle", html)
        self.assertIn("data-rd-search-panel", html)
        self.assertIn("rd-search-toggle__placeholder", html)
        self.assertIn(f'action="{reverse("set_language")}"', html)
        self.assertIn('id="rd-lang-mgmt"', html)
        self.assertIn("data-app-theme-toggle", html)
        self.assertIn(f'href="{reverse("sales:employee_entry")}"', html)
        self.assertIn("rd-user-menu-trigger", html)

    def test_dashboard_first_kpi_is_hero(self):
        html = self._dashboard()
        self.assertEqual(html.count("rd-stat--hero"), 1)
