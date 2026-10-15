"""The management shell (topbar + sidebar) renders the Pearl layout and keeps its JS hooks."""

import re
from datetime import datetime
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

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


class EmployeeShellTests(TestCase):
    """The employee shell: avatar menu (language + theme inside) · title · wallet chip."""

    @classmethod
    def setUpTestData(cls):
        from decimal import Decimal

        from employees.models import EmployeeProfile

        cls.user = User.objects.create_user("shell_emp", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={
                "role": UserProfile.Role.EMPLOYEE,
                "is_active_profile": True,
                "full_name": "Shell Worker",
            },
        )
        EmployeeProfile.objects.create(user=cls.user, monthly_salary=Decimal("1000"))

    def setUp(self):
        cache.clear()
        self.client.force_login(self.user)

    def _page(self, lang=None):
        self.addCleanup(translation.deactivate)
        lang = lang or settings.LANGUAGE_CODE
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = lang
        with translation.override(lang):
            url = reverse("sales:employee_entry")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def _header(self, html):
        match = re.search(r'<header class="rd-employee-topbar".*?</header>', html, re.S)
        self.assertIsNotNone(match, "employee top bar missing")
        return match.group(0)

    def test_topbar_order(self):
        header = self._header(self._page())
        menu = header.index("rd-user-menu")
        title = header.index("rd-employee-title")
        wallet = header.index("rd-employee-wallet")
        self.assertLess(menu, title)
        self.assertLess(title, wallet)

    def test_topbar_title_is_the_pages_h1(self):
        header = self._header(self._page())
        self.assertRegex(header, r'<h1 class="rd-employee-title">\s*<span>')
        self.assertEqual(header.count("<h1"), 1)

    def test_recent_page_has_exactly_one_h1(self):
        html = self.client.get(reverse("sales:employee_recent_sales")).content.decode()
        self.assertEqual(html.count("<h1"), 1)
        self.assertIn('<h1 class="rd-employee-title">', html)

    def test_title_h1_resets_bootstrap_heading_styles(self):
        css = (settings.BASE_DIR / "static/css/employee.css").read_text(encoding="utf-8")
        body = re.search(r"\n\.rd-employee-title \{([^}]*)\}", css).group(1)
        for decl in (
            "margin: 0;",
            "color: inherit;",
            "font-family: inherit;",
            "font-size: 1.1rem;",
            "font-weight: 700;",
            "line-height: 1.2;",
        ):
            self.assertIn(decl, body)

    def test_prefs_inside_user_menu(self):
        header = self._header(self._page())
        start = header.index('class="rd-user-menu-panel"')
        end = header.index("</details>", start)
        panel = header[start:end]
        outside = header[:start] + header[end:]
        for marker in ('id="rd-lang-emp"', "data-app-theme-toggle"):
            self.assertIn(marker, panel)
            self.assertNotIn(marker, outside)

    def test_prefs_are_a_labelled_group_inside_the_menu(self):
        header = self._header(self._page("en"))
        self.assertIn('<div class="rd-user-menu-prefs" role="group" aria-label="Settings">', header)
        ar = self._header(self._page("ar"))
        self.assertIn('class="rd-user-menu-prefs" role="group" aria-label="الإعدادات"', ar)

    def test_number_check_topbar_title_is_translated(self):
        self.addCleanup(translation.deactivate)
        for lang, title in (("en", "Check numbers"), ("ar", "فحص الأرقام")):
            with self.subTest(lang=lang):
                self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = lang
                with translation.override(lang):
                    url = reverse("sales:employee_refresh_number")
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                header = self._header(response.content.decode())
                self.assertRegex(header, r'<h1 class="rd-employee-title">\s*<span>%s</span>' % title)

    def test_employee_css_loaded(self):
        self.assertIn("css/employee.css", self._page())

    def test_number_check_label_translated(self):
        ar = self._page("ar")
        self.assertIn('<span class="rd-mobile-tabbar__label">فحص الأرقام</span>', ar)
        en = self._page("en")
        self.assertIn('<span class="rd-mobile-tabbar__label">Check numbers</span>', en)


class ManagementSidebarTests(TestCase):
    """Pearl sidebar: group icons, ink count chip, translated labels, 44 px mobile rows."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("side_mgr", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={
                "role": UserProfile.Role.MANAGEMENT,
                "is_active_profile": True,
                "full_name": "Side Manager",
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
        self.addCleanup(translation.deactivate)

    @staticmethod
    def _css():
        path = settings.BASE_DIR / "static" / "css" / "design-system.css"
        return path.read_text(encoding="utf-8")

    @staticmethod
    def _template():
        path = settings.BASE_DIR / "templates" / "partials" / "management_nav.html"
        return path.read_text(encoding="utf-8")

    def _nav(self, lang="en"):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = lang
        with translation.override(lang):
            url = reverse("reports:dashboard")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        match = re.search(r"<nav\b[^>]*data-rd-sidebar-nav.*?</nav>", html, re.S)
        self.assertIsNotNone(match)
        return match.group(0)

    def test_group_icons_visible(self):
        css = self._css()
        for match in re.finditer(r"[^{}]*\.rd-nav-group-icon\b[^{}]*\{([^}]*)\}", css):
            self.assertNotRegex(match.group(1), r"display:\s*none", match.group(0))
        nav = self._nav()
        toggles = re.findall(r'<button[^>]*data-rd-nav-group-toggle.*?</button>', nav, re.S)
        self.assertGreaterEqual(len(toggles), 6)
        for toggle in toggles:
            self.assertRegex(toggle, r'<span class="rd-nav-group-icon"[^>]*>\s*<svg', toggle)

    def test_group_labels_are_readable(self):
        css = self._css()
        rule = re.search(r"\.rd-sidebar-nav--accordion \.rd-nav-group-toggle \{([^}]*)\}", css)
        self.assertIsNotNone(rule)
        size = float(re.search(r"font-size:\s*([\d.]+)rem", rule.group(1)).group(1))
        self.assertGreaterEqual(size, 0.82)
        self.assertRegex(rule.group(1), r"font-weight:\s*600")

    def test_count_chip_is_ink(self):
        css = self._css()
        rule = re.search(r"\n\.rd-nav-count \{([^}]*)\}", css)
        self.assertIsNotNone(rule)
        body = rule.group(1)
        self.assertIn("background: var(--rd-primary-bg)", body)
        self.assertIn("color: var(--rd-primary-fg)", body)
        self.assertNotIn("--rd-danger", body)
        self.assertNotIn("--rd-on-danger", body)

    def test_untranslated_labels_use_trans(self):
        template = self._template()
        self.assertFalse("تقارير المزودين" in re.sub(r"\{% trans [^%]*%\}", "", template))
        self.assertIn('{% trans "API" %}', template)
        self.assertNotRegex(template, r"<span>API</span>")

    def test_provider_reports_label_translated(self):
        self.assertIn("تقارير المزودين", self._nav("ar"))
        self.assertIn("Provider reports", self._nav("en"))
        self.assertIn(">API<", self._nav("en"))

    def test_duplicate_labels_have_group_aria_label(self):
        for lang in ("en", "ar"):
            with self.subTest(lang=lang):
                nav = self._nav(lang)
                links = re.findall(
                    r'<a class="rd-nav-link[^"]*"([^>]*)>\s*<span class="rd-nav-icon".*?</span>\s*'
                    r'<span[^>]*>([^<]+)</span>',
                    nav,
                    re.S,
                )
                self.assertGreater(len(links), 20)
                by_text = {}
                for attrs, text in links:
                    by_text.setdefault(text.strip(), []).append(attrs)
                dupes = {t: a for t, a in by_text.items() if len(a) > 1}
                self.assertTrue(dupes, "expected duplicate labels (Settings, Reports)")
                for text, attr_list in dupes.items():
                    labels = []
                    for attrs in attr_list:
                        found = re.search(r'aria-label="([^"]+)"', attrs)
                        self.assertIsNotNone(found, f"{text!r} link lacks aria-label")
                        # WCAG label-in-name: the visible text stays inside the accessible name.
                        self.assertIn(text, found.group(1))
                        self.assertNotEqual(found.group(1), text)
                        labels.append(found.group(1))
                    self.assertEqual(len(labels), len(set(labels)), labels)

    def test_mobile_rows_meet_touch_target(self):
        css = self._css()
        blocks = re.findall(r"@media \(max-width: 991\.98px\) \{(.*?)\n\}\n", css, re.S)
        hits = [
            b for b in blocks
            if re.search(r"\.rd-nav-link\s*\{[^}]*min-height:\s*3\.45rem", b)
        ]
        self.assertTrue(hits, "nav links need min-height: 3.45rem below 992px")
        self.assertTrue(
            any(re.search(r"\.rd-nav-group-toggle\s*\{[^}]*min-height:\s*3\.45rem", b) for b in blocks),
            "group toggles need min-height: 3.45rem below 992px",
        )

    def test_pearl_link_metrics(self):
        css = self._css()
        rule = re.search(r"\n\.rd-sidebar-nav--accordion \.rd-nav-link \{([^}]*)\}", css)
        self.assertIsNotNone(rule)
        body = rule.group(1)
        self.assertIn("border-radius: 0.85rem", body)
        self.assertIn("padding: 0.6rem 0.75rem", body)
        self.assertIn("gap: 0.7rem", body)

    def test_nav_hooks_and_links_kept(self):
        nav = self._nav()
        for hook in ("data-rd-sidebar-nav", "data-rd-nav-pinned", "data-rd-nav-active-section"):
            self.assertIn(hook, nav)
        self.assertEqual(nav.count("data-rd-nav-group-toggle"), 6)
        self.assertEqual(nav.count('data-rd-nav-group="'), 6)
