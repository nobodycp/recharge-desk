"""
Project-wide UI conventions, checked over every template in the repo.

Boosted navigation and corner toasts are wired up once in the two shells
(base_management.html / base_employee.html). These tests fail when a new
template quietly opts out of them, and the failure says what to do instead.
"""

import re
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.test import SimpleTestCase

BASE_DIR = Path(settings.BASE_DIR)

STANDALONE_PAGES = {
    "templates/base_management.html",
    "templates/base_employee.html",
    "templates/accounts/login.html",
    "templates/core/forbidden.html",
    "phone_refresh/templates/phone_refresh/public_refresh.html",
}
SHELLS = {"templates/base_management.html", "templates/base_employee.html"}
FONT_PAGES = SHELLS | {"templates/accounts/login.html", "templates/core/forbidden.html"}
OUT_OF_SCOPE = {"phone_refresh/templates/phone_refresh/public_refresh.html"}
MESSAGES_PARTIAL = "templates/partials/messages.html"

SEVERITY_ALERT = re.compile(r"<[a-z]+\b[^>]*\balert-(success|danger)\b[^>]*>", re.I | re.S)
MESSAGES_LOOP = re.compile(r"{%\s*for\s+\w+\s+in\s+messages\s*%}")
CUSTOM_NONFIELD = re.compile(r"non_field_errors\s*\|\s*join|in\s+[\w.]*non_field_errors\s*%}")

# --- Pearl identity guardrails -------------------------------------------------
# The LEGACY_* sets list templates that still need migrating in later phases. They
# are computed once and may only SHRINK: fix a file, then delete it from its set
# (test_legacy_lists_only_shrink fails if a listed file no longer offends).
# Never add a new file to a LEGACY_* set; fix the template instead.
RAW_HEX = re.compile(r"#[0-9a-fA-F]{6}\b")
# A Bootstrap colour badge: a standalone `badge` class and a `bg-*` / `text-bg-*` class
# in the same tag, in either order. The `(?<![\w-])...(?![\w-])` guards keep `.rd-badge`
# and `rd-badge--paid` from matching.
_BADGE = r"(?<![\w-])badge(?![\w-])"
_BG = r"(?<![\w-])(?:text-bg|bg)-\w+"
BS_BADGE = re.compile(rf"{_BADGE}[^>]*?{_BG}|{_BG}[^>]*?{_BADGE}")
TABLE_LIGHT = re.compile(r"(?<![\w-])table-light(?![\w-])")

LEGACY_BADGES = {  # 9 files
    "templates/companies/layan_reconcile.html",
    "templates/companies/partials/layan_reconcile_review_table.html",
    "templates/companies/partials/layan_reconcile_settlements.html",
    "templates/companies/partials/layan_reconcile_table.html",
    "templates/companies/partials/sky_reconcile_table.html",
    "templates/companies/product_list.html",
    "templates/companies/sky_reconcile.html",
    "templates/core/search.html",
    "templates/inventory/_serials_paste_field.html",
}
LEGACY_TABLE_LIGHT = {  # 3 files
    "templates/core/search.html",
    "templates/customers/customer_detailed_invoice.html",
    "templates/customers/customer_statement.html",
}
LEGACY_HEX = {  # 1 file
    "templates/sms_gateway/settings_index.html",
}


def _template_files():
    roots = [BASE_DIR / "templates"]
    for config in apps.get_app_configs():
        path = Path(config.path) / "templates"
        if path.is_dir() and BASE_DIR in path.parents and "site-packages" not in path.parts:
            roots.append(path)
    for root in roots:
        for path in sorted(root.rglob("*.html")):
            yield path.relative_to(BASE_DIR).as_posix(), path.read_text(encoding="utf-8")


class TemplateConventionTests(SimpleTestCase):
    def _assert_none(self, offenders, advice):
        if offenders:
            self.fail(advice + "\n  " + "\n  ".join(offenders))

    def test_pages_extend_a_shell(self):
        offenders = [
            name
            for name, text in _template_files()
            if "<body" in text and name not in STANDALONE_PAGES
        ]
        self._assert_none(
            offenders,
            "These templates render their own <body>, so they get no boosted navigation "
            "and no toasts. Use {% extends \"base_management.html\" %} or "
            "{% extends \"base_employee.html\" %} instead:",
        )

    def test_shells_load_navigation_and_toasts(self):
        files = dict(_template_files())
        for name in SHELLS:
            text = files[name]
            self.assertIn('include "partials/nav_head.html"', text, name)
            self.assertIn("partials/nav_body_attrs.html", text, name)
            self.assertIn('include "partials/toast_region.html"', text, name)
        region = files["templates/partials/toast_region.html"]
        self.assertIn('include "partials/messages.html"', region)

    def test_page_scope_loads_before_htmx(self):
        head = (BASE_DIR / "templates/partials/nav_head.html").read_text(encoding="utf-8")
        scope, htmx = head.find("js/page-scope.js"), head.find("vendor/htmx.min.js")
        self.assertGreater(scope, -1, "nav_head.html must load js/page-scope.js")
        self.assertLess(scope, htmx, "js/page-scope.js must load before htmx")
        self.assertNotRegex(head, r"page-scope\.js[^>]*\b(defer|async)\b")

    def test_django_messages_render_only_as_toasts(self):
        offenders = [
            name
            for name, text in _template_files()
            if MESSAGES_LOOP.search(text) and name != MESSAGES_PARTIAL
        ]
        self._assert_none(
            offenders,
            "Django messages are shown as corner toasts by the shell. Don't loop over "
            "`messages` in a page; just call messages.success()/error() in the view:",
        )

    def test_no_success_or_error_boxes(self):
        offenders = []
        for name, text in _template_files():
            for match in SEVERITY_ALERT.finditer(text):
                if "data-rd-inline-alert" not in match.group(0):
                    line = text.count("\n", 0, match.start()) + 1
                    offenders.append(f"{name}:{line}")
        self._assert_none(
            offenders,
            "Success/error feedback is a toast, not an alert box. Use messages.success()/"
            "error() in the view, or "
            '{% include "partials/form_errors_toast.html" with errors=... %} in the '
            "template. Persistent state notes can use alert-warning/alert-info. If the box "
            "really is page content, add data-rd-inline-alert to it:",
        )

    def test_non_field_errors_render_as_toasts(self):
        offenders = [name for name, text in _template_files() if CUSTOM_NONFIELD.search(text)]
        self._assert_none(
            offenders,
            "Render form-wide errors with "
            '{% include "partials/form_errors_toast.html" with errors=form.non_field_errors %} '
            "(or plain {{ form.non_field_errors }}, which also becomes a toast):",
        )

    def test_no_legacy_error_banner(self):
        offenders = [name for name, text in _template_files() if "rd-form-error-banner" in text]
        self._assert_none(
            offenders,
            "rd-form-error-banner was replaced by toasts; use partials/form_errors_toast.html:",
        )

    def test_no_google_fonts(self):
        offenders = [
            name
            for name, text in _template_files()
            if "fonts.googleapis.com" in text and name not in OUT_OF_SCOPE
        ]
        self._assert_none(
            offenders,
            "Fonts are self-hosted; include partials/fonts_head.html instead:",
        )

    def test_pages_load_self_hosted_fonts(self):
        files = dict(_template_files())
        for name in FONT_PAGES:
            self.assertIn('include "partials/fonts_head.html"', files[name], name)

    def test_font_faces_point_at_shipped_files(self):
        css = (BASE_DIR / "static/css/fonts.css").read_text(encoding="utf-8")
        urls = re.findall(r"url\(\"?\.\./fonts/([^\")]+)\"?\)", css)
        self.assertTrue(urls)
        for url in urls:
            self.assertTrue((BASE_DIR / "static/fonts" / url).is_file(), url)

    def test_font_preloads_match_fonts_css_urls(self):
        head = (BASE_DIR / "templates/partials/fonts_head.html").read_text(encoding="utf-8")
        css = (BASE_DIR / "static/css/fonts.css").read_text(encoding="utf-8")
        preloads = re.findall(r"<link\b[^>]*\brel=\"preload\"[^>]*>", head)
        self.assertTrue(preloads)
        for tag in preloads:
            href = re.search(r"href=\"([^\"]+)\"", tag).group(1)
            self.assertNotIn(
                "static_cache_query",
                href,
                "Preload URLs must match the un-versioned url() in fonts.css exactly",
            )
            name = re.search(r"fonts/([\w.-]+\.woff2)", href).group(1)
            self.assertIn('url("../fonts/%s")' % name, css, name)

    # ---- Pearl identity guardrails (see DESIGN.md) ----

    def _offenders(self, pattern, legacy):
        found = {
            name
            for name, text in _template_files()
            if name not in OUT_OF_SCOPE and pattern.search(text)
        }
        return found, sorted(found - legacy)

    def test_no_raw_hex_colors_in_templates(self):
        _, offenders = self._offenders(RAW_HEX, LEGACY_HEX)
        self._assert_none(
            offenders,
            "Raw hex colours in templates bypass the Pearl tokens (and dark mode). Use a "
            "var(--rd-*) token or a utility/component class from design-system.css:",
        )

    def test_no_bootstrap_color_badges(self):
        _, offenders = self._offenders(BS_BADGE, LEGACY_BADGES)
        self._assert_none(
            offenders,
            "Bootstrap colour badges (badge bg-* / text-bg-*) are off-palette. Use "
            "`rd-badge rd-badge--paid|pending|cancelled|neutral` instead:",
        )

    def test_no_table_light(self):
        _, offenders = self._offenders(TABLE_LIGHT, LEGACY_TABLE_LIGHT)
        self._assert_none(
            offenders,
            "`table-light` is a Bootstrap grey header. `rd-table-modern` already styles the "
            "<thead>; drop `table-light`:",
        )

    def test_badge_regex_ignores_rd_badges(self):
        self.assertFalse(BS_BADGE.search('<span class="rd-badge rd-badge--paid">x</span>'))
        self.assertFalse(BS_BADGE.search('<div class="bg-white"><span class="rd-badge">x</span></div>'))
        for html in (
            '<span class="badge bg-success">x</span>',
            '<span class="badge text-bg-warning">x</span>',
            '<span class="bg-danger text-white badge">x</span>',
            '<span class="small badge rounded-pill bg-secondary">x</span>',
        ):
            self.assertTrue(BS_BADGE.search(html), html)

    def test_legacy_lists_only_shrink(self):
        for label, pattern, legacy in (
            ("LEGACY_BADGES", BS_BADGE, LEGACY_BADGES),
            ("LEGACY_TABLE_LIGHT", TABLE_LIGHT, LEGACY_TABLE_LIGHT),
            ("LEGACY_HEX", RAW_HEX, LEGACY_HEX),
        ):
            found, _ = self._offenders(pattern, legacy)
            stale = sorted(legacy - found)
            self._assert_none(
                stale,
                f"These files no longer offend, so remove them from {label} "
                "(the legacy lists may only shrink):",
            )
            self.assertFalse(legacy & OUT_OF_SCOPE, label)
