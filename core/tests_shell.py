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
        # R1: language + theme live inside the user-menu panel (see ManagementTopbarTests).
        self.assertIn(f'action="{reverse("set_language")}"', html)
        self.assertIn('id="rd-lang-mgmt"', html)
        self.assertIn('name="language" value="ar"', html)
        self.assertIn('name="language" value="en"', html)
        self.assertIn("data-app-theme-toggle", html)
        panel = re.search(r'<div class="rd-user-menu-panel" role="menu">.*?</details>', html, re.S)
        self.assertIsNotNone(panel, "user menu panel missing")
        self.assertIn('id="rd-lang-mgmt"', panel.group(0))
        self.assertIn("data-app-theme-toggle", panel.group(0))
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


def _media_blocks(css, query):
    """Bodies of every top-level `@media (<query>) { ... }` block (brace matched)."""
    blocks = []
    needle = f"@media ({query})"
    pos = 0
    while True:
        start = css.find(needle, pos)
        if start == -1:
            return blocks
        i = css.index("{", start)
        depth, j = 1, i + 1
        while depth:
            depth += {"{": 1, "}": -1}.get(css[j], 0)
            j += 1
        blocks.append(css[i + 1 : j - 1])
        pos = j


def _rules(css):
    """(selector, body) for the flat rules of a CSS fragment."""
    return [(m.group(1).strip(), m.group(2)) for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css)]


def _find_rules(css, selector):
    return [body for sel, body in _rules(css) if selector in sel]


class ManagementTopbarTests(TestCase):
    """Pearl top bar (mockup C): prefs in the user menu, 44 px targets, viewport-safe panels."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("bar_mgr", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={
                "role": UserProfile.Role.MANAGEMENT,
                "is_active_profile": True,
                "full_name": "Bar Manager",
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
        return (settings.BASE_DIR / "static" / "css" / "design-system.css").read_text(encoding="utf-8")

    def _header(self, lang="en"):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = lang
        with translation.override(lang):
            url = reverse("reports:dashboard")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        match = re.search(r'<header class="rd-topbar">.*?</header>', html, re.S)
        self.assertIsNotNone(match, "management top bar missing")
        return match.group(0)

    def test_prefs_inside_management_user_menu(self):
        header = self._header()
        start = header.index('class="rd-user-menu-panel" role="menu"')
        end = header.index("</details>", start)
        panel = header[start:end]
        outside = header[:start] + header[end:]
        for marker in ('id="rd-lang-mgmt"', "data-app-theme-toggle"):
            self.assertIn(marker, panel)
            self.assertNotIn(marker, outside)
        self.assertIn('<div class="rd-user-menu-prefs" role="group" aria-label="Settings">', panel)
        self.assertIn(f'action="{reverse("set_language")}"', panel)
        self.assertIn('name="next"', panel)
        self.assertNotIn("onchange", panel)
        self.assertNotIn("<select", panel)
        self.assertIn('hx-boost="false"', panel)
        self.assertIn("csrfmiddlewaretoken", panel)

    def test_language_toggle_is_an_ar_en_segmented_control(self):
        for lang, pressed in (("en", "en"), ("ar", "ar")):
            with self.subTest(lang=lang):
                header = self._header(lang)
                form = re.search(r'<form[^>]*id="rd-lang-mgmt".*?</form>', header, re.S).group(0)
                self.assertIn(f'action="{reverse("set_language")}"', form)
                self.assertIn('hx-boost="false"', form)
                self.assertIn('name="next"', form)
                group = re.search(r'<div class="rd-lang-toggle"[^>]*>', form).group(0)
                self.assertIn('role="group"', group)
                self.assertIn('dir="ltr"', group)
                self.assertIn("aria-labelledby", group)
                buttons = re.findall(r"<button[^>]*>[^<]*</button>", form)
                self.assertEqual(len(buttons), 2)
                self.assertLess(form.index('value="ar"'), form.index('value="en"'))
                for button, (code, text, name) in zip(
                    buttons, (("ar", "AR", "العربية"), ("en", "EN", "English"))
                ):
                    self.assertIn('type="submit"', button)
                    self.assertIn('name="language"', button)
                    self.assertIn(f'value="{code}"', button)
                    self.assertIn(f'lang="{code}"', button)
                    self.assertIn(f'aria-label="{name}"', button)
                    self.assertTrue(button.endswith(f">{text}</button>"), button)
                    active = code == pressed
                    self.assertIn(f'aria-pressed="{str(active).lower()}"', button)
                    self.assertEqual("is-active" in button, active, button)

    def test_language_toggle_css(self):
        css = self._css()
        active = "\n".join(_find_rules(css, ".rd-lang-toggle__btn.is-active"))
        self.assertIn("background: var(--rd-primary-bg)", active)
        self.assertIn("color: var(--rd-primary-fg)", active)
        self.assertTrue(_find_rules(css, ".rd-lang-toggle__btn:focus-visible"))
        mobile = "\n".join(_media_blocks(css, "max-width: 991.98px"))
        heights = [float(v) for v in re.findall(r"min-height:\s*([\d.]+)rem", "\n".join(_find_rules(mobile, ".rd-lang-toggle__btn")))]
        self.assertTrue(heights and max(heights) >= 3.45, heights)

    def test_header_avatar_is_constrained(self):
        css = self._css()
        desktop = re.sub(r"@media[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", "", css)
        avatar = "\n".join(_find_rules(desktop, ".rd-topbar .rd-user-menu--ava .rd-avatar"))
        self.assertRegex(avatar, r"width:\s*2\.6rem")
        self.assertRegex(avatar, r"height:\s*2\.6rem")
        img = "\n".join(_find_rules(desktop, ".rd-topbar .rd-user-menu--ava .rd-avatar img"))
        for decl in ("max-width: 100%", "max-height: 100%", "object-fit: cover", "border-radius: 50%"):
            self.assertIn(decl, img)
        mobile = "\n".join(_media_blocks(css, "max-width: 991.98px"))
        # Only the trigger's avatar grows on phones; the avatar in the open menu
        # header keeps its 2.6rem size.
        small = "\n".join(_find_rules(mobile, ".rd-topbar .rd-user-menu--ava .rd-user-menu-trigger .rd-avatar"))
        self.assertRegex(small, r"width:\s*3\.45rem")
        self.assertRegex(small, r"height:\s*3\.45rem")
        self.assertEqual(_find_rules(mobile, ".rd-topbar .rd-user-menu--ava .rd-avatar"), [])
        menu = "\n".join(_find_rules(desktop, ".rd-topbar .rd-user-menu-header .rd-avatar"))
        self.assertRegex(menu, r"width:\s*2\.6rem")
        base = "\n".join(_find_rules(desktop, ".rd-avatar img"))
        self.assertIn("object-fit: cover", base)
        self.assertIn("max-width: 100%", base)

    def test_header_avatar_markup_photo_and_initials(self):
        header = self._header()
        pattern = r'<details class="rd-user-menu rd-user-menu--ava">\s*<summary.*?</summary>'
        trigger = re.search(pattern, header, re.S).group(0)
        self.assertNotIn("<img", trigger)
        self.assertIn("<span>B</span>", trigger)
        UserProfile.objects.filter(user=self.user).update(avatar="avatars/me.png")
        header = self._header()
        trigger = re.search(pattern, header, re.S).group(0)
        self.assertIn('<img src="/media/avatars/me.png" alt="">', trigger)
        panel = header[header.index('class="rd-user-menu-panel" role="menu"'):]
        self.assertIn('<img src="/media/avatars/me.png" alt="">', panel)

    def test_topbar_row_order_and_sales_entry(self):
        header = self._header()
        order = [
            header.index("rd-hello"),
            header.index("data-rd-search-toggle"),
            header.index("data-rd-notifications"),
            header.index("rd-topbar-cta"),
            header.index("rd-user-menu--ava"),
        ]
        self.assertEqual(order, sorted(order))
        cta = re.search(r'<a[^>]*rd-topbar-cta[^>]*>.*?</a>', header, re.S).group(0)
        self.assertIn(f'href="{reverse("sales:employee_entry")}"', cta)
        self.assertIn('class="rd-topbar-cta__label">Sales entry</span>', cta)
        self.assertIn("rd-topbar-cta__plus", cta)
        self.assertNotIn("<h1", header)

    def test_topbar_touch_targets(self):
        css = self._css()
        targets = (
            ".rd-search-toggle",
            ".rd-notif-trigger",
            ".rd-user-menu--ava .rd-user-menu-trigger",
            ".rd-menu-btn",
            ".rd-topbar-cta",  # wide (label), so only its height is pinned
        )
        mobile = "\n".join(_media_blocks(css, "max-width: 991.98px"))
        for sel in targets:
            with self.subTest(sel=sel, viewport="mobile"):
                bodies = _find_rules(mobile, sel)
                self.assertTrue(bodies, f"{sel} missing in the <992px block")
                joined = "\n".join(bodies)
                if sel != ".rd-topbar-cta":
                    self.assertRegex(joined, r"min-width:\s*3\.45rem")
                self.assertRegex(joined, r"min-height:\s*3\.45rem")
        for sel in targets:
            with self.subTest(sel=sel, viewport="desktop"):
                bodies = _find_rules(re.sub(r"@media[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", "", css), sel)
                joined = "\n".join(bodies)
                for prop in ("min-height",) if sel == ".rd-topbar-cta" else ("min-width", "min-height"):
                    values = [float(v) for v in re.findall(prop + r":\s*([\d.]+)rem", joined)]
                    self.assertTrue(values and max(values) >= 2.6, f"{sel} {prop} {values}")

    def test_panels_use_viewport_safe_positioning(self):
        css = self._css()
        small = "\n".join(_media_blocks(css, "max-width: 575.98px"))
        for sel in (".rd-notif-menu .rd-user-menu-panel", ".rd-search-panel", ".rd-user-menu--ava .rd-user-menu-panel"):
            with self.subTest(sel=sel):
                joined = "\n".join(_find_rules(small, sel))
                # Anchored to the sticky bar, not `fixed` (which would depend on
                # the bar's backdrop-filter creating a containing block).
                self.assertIn("position: absolute", joined)
                self.assertNotIn("position: fixed", joined)
                self.assertRegex(joined, r"inset-inline:\s*0?\.5rem")
                self.assertNotRegex(joined, r"(?<![\w-])width:\s*\d")

    def test_phone_sheets_anchor_to_the_bar_without_backdrop_filter(self):
        css = self._css()
        small = "\n".join(_media_blocks(css, "max-width: 575.98px"))
        # Intermediate wrappers must not be positioned, or the sheet would
        # anchor to the toggle/trigger instead of the bar.
        for sel in (".rd-search", ".rd-topbar .rd-user-menu"):
            with self.subTest(sel=sel):
                self.assertIn("position: static", "\n".join(_find_rules(small, sel)))
        bar = "\n".join(_find_rules(css, ".rd-topbar"))
        self.assertIn("position: sticky", bar)  # the positioned ancestor

    def test_menu_controls_meet_touch_target_on_phones(self):
        css = self._css()
        mobile = "\n".join(_media_blocks(css, "max-width: 991.98px"))
        for sel in (
            ".rd-user-menu-panel .rd-user-menu-item",  # links, logout button, notification rows
            ".rd-user-menu-pref .rd-theme-btn",
            ".rd-lang-toggle__btn",
            ".rd-search-item",
            ".rd-search-more",
        ):
            with self.subTest(sel=sel):
                joined = "\n".join(_find_rules(mobile, sel))
                self.assertRegex(joined, r"min-height:\s*3\.45rem")
        lang = "\n".join(_find_rules(mobile, ".rd-lang-toggle__btn"))
        self.assertRegex(lang, r"min-width:\s*3\.45rem")
        item = "\n".join(_find_rules(mobile, ".rd-search-item"))
        self.assertIn("display: flex", item)
        self.assertIn("flex-direction: column", item)
        # Labels are nowrap + ellipsis: they must stretch to the row width,
        # or long names overflow the panel instead of truncating.
        self.assertNotRegex(item, r"align-items:\s*(flex-)?start")
        more = "\n".join(_find_rules(mobile, ".rd-search-more"))
        self.assertIn("display: flex", more)
        theme = "\n".join(_find_rules(mobile, ".rd-user-menu-pref .rd-theme-btn"))
        self.assertRegex(theme, r"min-width:\s*3\.45rem")

    def test_topbar_triggers_are_top_aligned_in_their_wrapper(self):
        rule = "\n".join(_find_rules(self._css(), ".rd-topbar .rd-user-menu > summary"))
        self.assertIn("vertical-align: top", rule)

    def test_phone_sheets_leave_a_bottom_gap(self):
        css = self._css()
        small = "\n".join(_media_blocks(css, "max-width: 575.98px"))
        for sel in (".rd-search .rd-search-panel", ".rd-topbar .rd-user-menu--ava .rd-user-menu-panel"):
            with self.subTest(sel=sel):
                joined = "\n".join(_find_rules(small, sel))
                self.assertRegex(joined, r"max-height:\s*calc\(100vh - 8rem\)")
                self.assertRegex(joined, r"max-height:\s*calc\(100dvh - 8rem\)")
                self.assertLess(joined.index("100vh"), joined.index("100dvh"))

    def test_panels_use_raised_surface_and_pop_shadow(self):
        css = self._css()
        for sel in (".rd-user-menu-panel", ".rd-search-panel"):
            with self.subTest(sel=sel):
                bodies = [b for s_, b in _rules(css) if s_.strip().endswith(sel) and "background:" in b and "position: absolute" in b]
                self.assertEqual(len(bodies), 1, sel)
                self.assertIn("background: var(--rd-surface-raised)", bodies[0])
                self.assertIn("box-shadow: var(--rd-shadow-pop)", bodies[0])
                self.assertIn("border: 1px solid", bodies[0])

    def test_search_input_text_is_plainly_readable(self):
        css = self._css()
        base = "\n".join(body for s_, body in _rules(css) if s_.strip() == ".rd-search-input")
        self.assertIn("color: var(--rd-ink)", base)
        self.assertIn("-webkit-text-fill-color: var(--rd-ink)", base)
        self.assertIn("caret-color: var(--rd-ink)", base)
        selection = "\n".join(_find_rules(css, ".rd-search-input::selection"))
        self.assertIn("color: var(--rd-ink)", selection)
        self.assertIn("background: color-mix(", selection)  # token-based, never the UA grey
        autofill = "\n".join(_find_rules(css, ".rd-search-input:-webkit-autofill"))
        self.assertIn("-webkit-text-fill-color: var(--rd-ink)", autofill)

    def test_search_has_a_single_scroll_box(self):
        css = self._css()
        for sel, body in _rules(css):
            if ".rd-search-results" in sel and "[hidden]" not in sel:
                self.assertNotRegex(body, r"max-height|overflow", f"{sel} must not scroll on its own")
        for query in ("min-width: 992px", "max-width: 575.98px"):
            for block in _media_blocks(css, query):
                for sel, body in _rules(block):
                    if sel.strip() == ".rd-search-results":
                        self.assertNotRegex(body, r"max-height|overflow")
        base = "\n".join(body for sel, body in _rules(css) if sel.strip() == ".rd-search-panel")
        self.assertRegex(base, r"overflow-y:\s*auto")
        self.assertRegex(base, r"max-height:")

    def test_search_panel_desktop_width(self):
        css = self._css()
        wide = "\n".join(_media_blocks(css, "min-width: 992px"))
        joined = "\n".join(_find_rules(wide, ".rd-search-panel"))
        sizes = [float(v) for v in re.findall(r"([\d.]+)rem\)", joined)]
        self.assertTrue(sizes and max(sizes) >= 22, sizes)
        self.assertRegex(joined, r"max-height:")
        self.assertRegex(joined, r"overflow-y:\s*auto")

    def test_desktop_search_types_in_the_bar_itself(self):
        # The open panel sits on top of the bar: its field lands exactly where
        # the bar is (panel padding 0.4rem + 1px border), same height.
        css = self._css()
        wide = "\n".join(_media_blocks(css, "min-width: 992px"))
        panel = "\n".join(_find_rules(wide, ".rd-search .rd-search-panel"))
        self.assertIn("top: calc(-0.4rem - 1px)", panel)
        self.assertIn("inset-inline-start: calc(-0.4rem - 1px)", panel)
        self.assertIn("width: max(calc(100% + 0.8rem + 2px), 22rem)", panel)
        field = "\n".join(_find_rules(wide, ".rd-search-field"))
        self.assertRegex(field, r"min-height:\s*2\.6rem")

    def test_search_input_uses_the_bar_placeholder(self):
        html = self._header()
        bar = re.search(r'rd-search-toggle__placeholder">([^<]+)<', html).group(1)
        field = re.search(r'id="rd-global-search"[^>]*placeholder="([^"]+)"', html, re.S).group(1)
        self.assertEqual(bar, field)

    def test_zero_count_notification_rows_are_muted_not_hidden(self):
        css = self._css()
        self.assertRegex(css, r"\.rd-notif-item:has\(\.rd-notif-count:not\(\.is-active\)\)\s*\{[^}]*color:\s*var\(--rd-muted\)")
        self.assertNotRegex(css, r"\.rd-notif-item[^{]*\{[^}]*display:\s*none")

    def test_sales_entry_label_hidden_visually_on_phones(self):
        css = self._css()
        small = "\n".join(_media_blocks(css, "max-width: 575.98px"))
        label = "\n".join(_find_rules(small, ".rd-topbar-cta__label"))
        self.assertIn("clip", label)
        self.assertNotIn("display: none", label)
        plus = "\n".join(_find_rules(small, ".rd-topbar-cta__plus"))
        self.assertIn("display: block", plus)
        self.assertIn("display: none", "\n".join(_find_rules(css, ".rd-topbar-cta__plus")))

    def test_topbar_has_no_raw_hex(self):
        self.assertFalse(re.search(r"#[0-9a-fA-F]{3,8}\b", "\n".join(_find_rules(self._css(), ".rd-topbar"))))
