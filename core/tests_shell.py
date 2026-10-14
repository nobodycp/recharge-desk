"""The management shell (topbar + sidebar) renders the Pearl layout and keeps its JS hooks."""

import re
from datetime import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from accounts.models import UserProfile

User = get_user_model()


def _clock_at(hour):
    """Stand-in for the `datetime` that `{% now %}` reads (it ignores timezone.now)."""

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 14, hour, 30, tzinfo=tz)

    return Clock



class ManagementShellTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("shell_mgr", password="x", first_name="Sami")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={
                "role": UserProfile.Role.MANAGEMENT,
                "is_active_profile": True,
                "full_name": "Hamza Naeem",
            },
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

    def _greeting(self, hour=None):
        if hour is None:
            html = self._dashboard()
        else:
            with mock.patch("django.template.defaulttags.datetime", _clock_at(hour)):
                html = self._dashboard()
        match = re.search(r'<span class="rd-hello__title">(.*?)</span>', html, re.S)
        self.assertIsNotNone(match, "greeting title span missing")
        return match.group(1)

    def test_topbar_greeting(self):
        html = self._dashboard()
        self.assertIn('class="rd-hello', html)
        self.assertIn("rd-hello__date", html)

    def test_greeting_uses_profile_display_name(self):
        title = self._greeting()
        self.assertIn("Hamza", title)
        self.assertNotIn("Naeem", title)
        self.assertNotIn("Sami", title)
        self.assertNotIn("shell_mgr", title)

    def test_greeting_falls_back_to_username(self):
        UserProfile.objects.filter(user=self.user).update(full_name="")
        User.objects.filter(pk=self.user.pk).update(first_name="")
        self.assertIn("shell_mgr", self._greeting())

    def test_greeting_hour_thresholds(self):
        for hour, expected in [
            (0, "Good morning"),
            (11, "Good morning"),
            (12, "Good afternoon"),
            (17, "Good afternoon"),
            (18, "Good evening"),
            (23, "Good evening"),
        ]:
            with self.subTest(hour=hour):
                self.assertIn(expected, self._greeting(hour))

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
