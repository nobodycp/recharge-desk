"""Owner UI round (2026-10-15): one-tap language toggle, theme + language in both
top bars, login logo without a plate, compact number-check row."""

import re
import shutil
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import translation

from accounts.models import UserProfile
from core.models import SiteBranding
from core.tests_brand_logo import _png
from core.tests_shell import _find_rules, _media_blocks

User = get_user_model()
CSS_DIR = Path(settings.BASE_DIR) / "static" / "css"


def _css(name):
    return (CSS_DIR / name).read_text(encoding="utf-8")


def _desktop(css):
    return re.sub(r"@media[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", "", css)


class LangToggleTests(TestCase):
    def _render(self, lang, path="/somewhere/?x=1"):
        request = RequestFactory().get(path)
        with translation.override(lang):
            return render_to_string("partials/lang_toggle.html", {"request": request}, request=request)

    def test_shows_the_other_language_in_arabic_ui(self):
        html = self._render("ar")
        self.assertIn('name="language" value="en"', html)
        self.assertNotIn('value="ar"', html)
        self.assertRegex(html, r'<span[^>]*lang="en"[^>]*>EN</span>')
        self.assertIn('aria-label="EN — التبديل إلى الإنجليزية"', html)
        self.assertIn('title="التبديل إلى الإنجليزية"', html)

    def test_shows_the_other_language_in_english_ui(self):
        html = self._render("en")
        self.assertIn('name="language" value="ar"', html)
        self.assertNotIn('value="en"', html)
        self.assertRegex(html, r'<span[^>]*lang="ar"[^>]*>AR</span>')
        self.assertIn('aria-label="AR — Switch to Arabic"', html)
        self.assertIn('title="Switch to Arabic"', html)

    def test_is_a_full_navigation_post_form_with_next(self):
        html = self._render("ar", "/somewhere/?x=1")
        form = re.search(r"<form[^>]*>", html).group(0)
        self.assertIn(f'action="{reverse("set_language")}"', form)
        self.assertIn('method="post"', form)
        self.assertIn('hx-boost="false"', form)
        self.assertIn("csrfmiddlewaretoken", html)
        self.assertRegex(html, r'name="next" value="/somewhere/\?x=1"')
        self.assertEqual(html.count("<button"), 1)
        self.assertEqual(html.count("<select"), 0)

    def test_looks_like_the_theme_button(self):
        html = self._render("ar")
        button = re.search(r"<button[^>]*>", html).group(0)
        self.assertIn("rd-theme-btn", button)
        self.assertIn("rd-lang-btn", button)
        self.assertIn('type="submit"', button)

    def test_other_language_comes_from_settings(self):
        with override_settings(LANGUAGES=[("en", "English"), ("de", "Deutsch")]):
            html = self._render("en")
        self.assertIn('value="de"', html)

    def test_label_and_translations(self):
        po = (Path(settings.BASE_DIR) / "locale/ar/LC_MESSAGES/django.po").read_text(encoding="utf-8")
        self.assertIn('msgid "Switch to English"\nmsgstr "التبديل إلى الإنجليزية"', po)
        self.assertIn('msgid "Switch to Arabic"\nmsgstr "التبديل إلى العربية"', po)

    def test_css_matches_theme_button_touch_target(self):
        css = _css("design-system.css")
        self.assertTrue(_find_rules(css, ".rd-lang-btn"))
        mobile = "\n".join(_media_blocks(css, "max-width: 991.98px"))
        topbar = "\n".join(_find_rules(mobile, ".rd-topbar .rd-lang-btn") or _find_rules(mobile, ".rd-topbar .rd-theme-btn"))
        self.assertRegex(topbar, r"min-height:\s*3\.45rem")
        self.assertRegex(topbar, r"min-width:\s*3\.45rem")

    def test_login_toggles_meet_touch_target_on_phones(self):
        mobile = "\n".join(_media_blocks(_css("login.css"), "max-width: 991.98px"))
        rules = "\n".join(_find_rules(mobile, ".rd-login-controls .rd-theme-btn"))
        self.assertRegex(rules, r"min-height:\s*3\.45rem")
        self.assertRegex(rules, r"min-width:\s*3\.45rem")

    def test_employee_tool_tap_areas_do_not_overlap(self):
        css = _css("employee.css")
        tools = "\n".join(_find_rules(css, ".rd-employee-topbar__tools"))
        after = "\n".join(_find_rules(css, ".rd-employee-topbar__tools .rd-theme-btn::after"))
        gap = float(re.search(r"gap:\s*([\d.]+)rem", tools).group(1))
        reach = float(re.search(r"inset:\s*-[\d.]+rem\s+-([\d.]+)rem", after).group(1))
        self.assertGreaterEqual(gap, 2 * reach)


class _Branding:
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        cache.clear()

    def branding_with_logo(self):
        branding = SiteBranding.load()
        branding.logo = SimpleUploadedFile(
            "mark.png",
            _png([(0, 0, 0, 255), (200, 0, 0, 255), (0, 0, 0, 0), (0, 0, 0, 255)]),
            content_type="image/png",
        )
        branding.save()
        return SiteBranding.objects.get(pk=1)


class ManagementTopbarPrefsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("own_mgr", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True, "full_name": "Own Manager"},
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
        self.addCleanup(translation.deactivate)
        self.client.force_login(self.user)

    def _header(self, lang):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = lang
        with translation.override(lang):
            url = reverse("reports:dashboard")
        html = self.client.get(url).content.decode()
        return re.search(r'<header class="rd-topbar">.*?</header>', html, re.S).group(0)

    def test_theme_and_language_buttons_sit_in_the_cluster_not_the_menu(self):
        for lang, target in (("ar", "en"), ("en", "ar")):
            with self.subTest(lang=lang):
                header = self._header(lang)
                menu = header.index('<details class="rd-user-menu rd-user-menu--ava"')
                menu_end = header.index("</details>", menu)
                menu_html = header[menu:menu_end]
                outside = header[:menu] + header[menu_end:]
                cluster = header[header.index('class="rd-topbar-cluster"'):]
                self.assertIn("data-app-theme-toggle", outside)
                self.assertIn(f'name="language" value="{target}"', outside)
                self.assertGreater(header.index("data-app-theme-toggle"), header.index('class="rd-topbar-cluster"'))
                self.assertIn("data-app-theme-toggle", cluster)
                for marker in ("data-app-theme-toggle", 'name="language"', "rd-user-menu-prefs", "rd-lang-toggle"):
                    self.assertNotIn(marker, menu_html)
                self.assertNotIn("rd-lang-toggle", header)
                self.assertEqual(header.count('name="language"'), 1)

    def test_menu_keeps_identity_settings_and_logout(self):
        header = self._header("en")
        start = header.index('<details class="rd-user-menu rd-user-menu--ava"')
        menu = header[start:header.index("</details>", start)]
        self.assertIn("rd-user-menu-identity", menu)
        self.assertIn("Own Manager", menu)
        self.assertIn(f'href="{reverse("accounts:account_settings")}"', menu)
        self.assertIn(f'action="{reverse("accounts:logout")}"', menu)


class EmployeeTopbarPrefsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from employees.models import EmployeeProfile

        cls.user = User.objects.create_user("own_emp", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.EMPLOYEE, "is_active_profile": True, "full_name": "Own Worker"},
        )
        EmployeeProfile.objects.create(user=cls.user, monthly_salary=Decimal("1000"))

    def setUp(self):
        cache.clear()
        self.addCleanup(translation.deactivate)
        self.client.force_login(self.user)

    def _header(self, lang):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = lang
        with translation.override(lang):
            url = reverse("sales:employee_entry")
        html = self.client.get(url).content.decode()
        return re.search(r'<header class="rd-employee-topbar".*?</header>', html, re.S).group(0)

    def test_theme_and_language_buttons_sit_in_the_bar_not_the_menu(self):
        for lang, target in (("ar", "en"), ("en", "ar")):
            with self.subTest(lang=lang):
                header = self._header(lang)
                menu = header.index('<details class="rd-user-menu rd-user-menu--ava"')
                menu_end = header.index("</details>", menu)
                menu_html = header[menu:menu_end]
                outside = header[:menu] + header[menu_end:]
                self.assertIn("data-app-theme-toggle", outside)
                self.assertIn(f'name="language" value="{target}"', outside)
                for marker in ("data-app-theme-toggle", 'name="language"', "rd-user-menu-prefs", "rd-lang-toggle"):
                    self.assertNotIn(marker, menu_html)
                self.assertEqual(header.count('name="language"'), 1)

    def test_order_avatar_title_tools_wallet(self):
        header = self._header("en")
        order = [
            header.index("rd-user-menu--ava"),
            header.index("rd-employee-title"),
            header.index("data-app-theme-toggle"),
            header.index('name="language"'),
            header.index("rd-employee-wallet"),
        ]
        self.assertEqual(order, sorted(order))

    def test_menu_keeps_identity_settings_and_logout(self):
        header = self._header("en")
        start = header.index('<details class="rd-user-menu rd-user-menu--ava"')
        menu = header[start:header.index("</details>", start)]
        self.assertIn("rd-user-menu-identity", menu)
        self.assertIn("Own Worker", menu)
        self.assertIn(f'href="{reverse("accounts:account_settings")}"', menu)
        self.assertIn(f'action="{reverse("accounts:logout")}"', menu)

    def test_bar_does_not_grow(self):
        css = _css("employee.css")
        row = "\n".join(_find_rules(css, ".rd-employee-topbar__row"))
        self.assertIn("height: 3.1rem", row)
        tools = "\n".join(_find_rules(_desktop(css), ".rd-employee-topbar__tools .rd-theme-btn"))
        self.assertRegex(tools, r"width:\s*2\.3rem")
        self.assertRegex(tools, r"height:\s*2\.3rem")
        self.assertTrue(_find_rules(css, ".rd-employee-topbar__tools .rd-theme-btn::after"))


class UserMenuPrefsRemovedCssTests(TestCase):
    def test_segmented_control_css_is_gone(self):
        css = _css("design-system.css")
        self.assertNotIn(".rd-lang-toggle", css)
        self.assertNotIn(".rd-user-menu-prefs", css)
        self.assertNotIn(".rd-user-menu-pref ", css)

    def test_login_select_css_is_gone(self):
        self.assertNotIn("rd-login-lang", _css("login.css"))

    def test_user_menu_has_no_prefs_block(self):
        html = (Path(settings.BASE_DIR) / "templates/partials/user_menu.html").read_text(encoding="utf-8")
        self.assertNotIn("prefs", html)
        self.assertNotIn("rd-lang-toggle", html)


class LoginLogoTests(_Branding, TestCase):
    def _login(self, lang="ar"):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = lang
        self.addCleanup(translation.deactivate)
        with translation.override(lang):
            url = reverse("accounts:login")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_language_is_a_single_toggle_not_a_select(self):
        html = self._login("ar")
        controls = re.search(r'<div class="rd-login-controls">.*?</form>\s*</div>', html, re.S).group(0)
        self.assertNotIn("<select", html)
        self.assertEqual(controls.count('name="language"'), 1)
        self.assertIn('name="language" value="en"', controls)
        self.assertIn("data-app-theme-toggle", controls)
        html_en = self._login("en")
        self.assertIn('name="language" value="ar"', html_en)

    def test_hero_logo_uses_the_dark_copy_without_plate(self):
        branding = self.branding_with_logo()
        html = self._login()
        hero = html[html.index('class="rd-login-brand rd-login-hero"'):]
        hero = hero[: hero.index("</aside>")]
        self.assertIn(f'src="{branding.logo_dark_url}"', hero)
        self.assertNotIn(f'src="{branding.logo.url}"', hero)
        self.assertIn('class="rd-login-logo"', hero)
        self.assertNotIn("rd-login-plate", html)

    def test_hero_logo_falls_back_to_the_original_when_no_dark_copy(self):
        branding = self.branding_with_logo()
        with mock.patch.object(SiteBranding, "logo_dark_url", new=None):
            cache.clear()
            html = self._login()
        hero = html[html.index('class="rd-login-brand rd-login-hero"'):]
        self.assertIn(f'src="{branding.logo.url}"', hero[: hero.index("</aside>")])

    def test_mini_logo_is_a_light_dark_pair(self):
        branding = self.branding_with_logo()
        html = self._login()
        mini = html[html.index('class="rd-login-mini"'):]
        mini = mini[: mini.index('class="rd-login-controls"')]
        imgs = re.findall(r"<img[^>]*>", mini)
        self.assertEqual(len(imgs), 2)
        light = [i for i in imgs if "rd-login-mini-logo--light" in i]
        dark = [i for i in imgs if "rd-login-mini-logo--dark" in i]
        self.assertEqual(len(light), 1)
        self.assertEqual(len(dark), 1)
        self.assertIn(f'src="{branding.logo.url}"', light[0])
        self.assertIn(f'src="{branding.logo_dark_url}"', dark[0])
        self.assertNotIn("rd-login-plate", mini)

    def test_letter_marks_remain_without_logo(self):
        html = self._login()
        self.assertEqual(len(re.findall(r'<span class="rd-login-mark[ "]', html)), 2)  # hero + mini
        self.assertNotIn("rd-login-logo", html)
        self.assertNotIn("rd-login-mini-logo", html)

    def test_css_has_no_plate_and_pairs_the_mini_logo(self):
        css = _css("login.css")
        self.assertNotIn(".rd-login-plate", css)
        self.assertIn(
            '[data-bs-theme="dark"] .rd-login-mini-logo--light,\n'
            ':root:not([data-bs-theme="dark"]) .rd-login-mini-logo--dark {\n'
            "  display: none;\n}",
            css,
        )


class NumberCheckRowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from employees.models import EmployeeProfile

        cls.user = User.objects.create_user("own_nc", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.EMPLOYEE, "is_active_profile": True, "full_name": "NC Worker"},
        )
        EmployeeProfile.objects.create(user=cls.user, monthly_salary=Decimal("1000"))

    def setUp(self):
        cache.clear()
        self.addCleanup(translation.deactivate)
        self.client.force_login(self.user)

    def _form(self, lang):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = lang
        with translation.override(lang):
            url = reverse("sales:employee_refresh_number")
        html = self.client.get(url).content.decode()
        start = html.index('id="rd-refresh-page"')
        return html[start: html.index('id="rd-refresh-result"', start)]

    def test_one_row_markup_in_arabic(self):
        form = self._form("ar")
        self.assertIn('class="rd-ncheck-row"', form)
        row = form[form.index('class="rd-ncheck-row"'):]
        input_at = row.index('id="rd-refresh-phone-input"')
        paste_at = row.index("employee-clipboard-paste-btn")
        refresh_at = row.index('id="rd-refresh-submit"')
        check_at = row.index('id="rd-check-submit"')
        self.assertLess(input_at, paste_at)
        self.assertLess(paste_at, check_at)
        self.assertLess(check_at, refresh_at)
        refresh = re.search(r'<button[^>]*id="rd-refresh-submit"[^>]*>(.*?)</button>', row, re.S)
        check = re.search(r'<button[^>]*id="rd-check-submit"[^>]*>(.*?)</button>', row, re.S)
        self.assertEqual(refresh.group(1).strip(), "تحديث")
        self.assertEqual(check.group(1).strip(), "فحص")
        self.assertIn("btn-primary", refresh.group(0))
        self.assertIn("btn-outline-secondary", check.group(0))

    def test_labels_in_english(self):
        form = self._form("en")
        self.assertRegex(form, r'id="rd-refresh-submit"[^>]*>\s*Refresh\s*</button>')
        self.assertRegex(form, r'id="rd-check-submit"[^>]*>\s*Check\s*</button>')

    def test_phone_input_contract_is_unchanged(self):
        form = self._form("en")
        field = re.search(r"<input[^>]*id=\"rd-refresh-phone-input\"[^>]*>", form, re.S).group(0)
        for attr in ('maxlength="10"', 'inputmode="numeric"', 'pattern="05[0123458][0-9]{7}"', 'placeholder="0501234567"', "autofocus", 'dir="ltr"'):
            self.assertIn(attr, field)
        self.assertIn('data-paste-target="rd-refresh-phone-input"', form)
        self.assertRegex(form, r'<label[^>]*for="rd-refresh-phone-input"[^>]*>\s*Phone number\s*</label>')
        self.assertIn('data-paste-fail="', form)

    def test_label_stays_above_the_row(self):
        form = self._form("en")
        self.assertLess(form.index('for="rd-refresh-phone-input"'), form.index('class="rd-ncheck-row"'))

    def test_label_meets_the_text_floor(self):
        label = "\n".join(_find_rules(_css("number-check.css"), ".rd-ncheck-label"))
        self.assertRegex(label, r"font-size:\s*0\.8rem")

    def test_css_row_input_sized_for_ten_digits(self):
        css = _css("number-check.css")
        row = "\n".join(_find_rules(css, ".rd-ncheck-row"))
        self.assertIn("display: flex", row)
        self.assertIn("flex-wrap: wrap", row)
        field = "\n".join(_find_rules(css, ".rd-ncheck-row .rd-entry-field"))
        self.assertRegex(field, r"inline-size:\s*[\d.]+ch|inline-size:\s*calc\(")
        self.assertNotIn("width: 100%", field)
        buttons = "\n".join(_find_rules(css, ".rd-ncheck-row .btn"))
        self.assertRegex(buttons, r"min-height:\s*3\.45rem")
        self.assertTrue(_find_rules(css, ".rd-ncheck-row"))
        self.assertTrue(_find_rules(css, ".rd-ncheck-row .rd-entry-field .form-control"))
        # The old two-column grid of actions is gone.
        self.assertNotIn(".rd-ncheck-actions", css)

    def test_translations(self):
        po = (Path(settings.BASE_DIR) / "locale/ar/LC_MESSAGES/django.po").read_text(encoding="utf-8")
        self.assertIn('msgid "Check"\nmsgstr "فحص"', po)
        self.assertIn('msgid "Refresh"\nmsgstr "تحديث"', po)
