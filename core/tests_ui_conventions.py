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

LEGACY_BADGES = {  # 1 file
    "templates/inventory/_serials_paste_field.html",
}
LEGACY_TABLE_LIGHT = set()  # 0 files
LEGACY_HEX = {  # 1 file
    "templates/sms_gateway/settings_index.html",
}


# --- Page kit guardrails (see core/templatetags/kit.py) ---------------------------
# Same shrink-only rule as above. Migrate a template onto the page kit, then delete
# it from its set. Never add a new file.
# A "page template" extends a shell (or a variable such as `base_template`) and
# defines {% block content %}.
EXTENDS_SHELL = re.compile(
    r"{%\s*extends\s+(?:[\"']base_(?:management|employee)\.html[\"']|base_template)\s*%}"
)
BLOCK_CONTENT = re.compile(r"{%\s*block\s+content\s*%}")
PAGE_HEADER = re.compile(r"{%\s*rd_page_header\b")
# Legacy card markup: `app-card…` or a Bootstrap `class="card"` / `class="card …"`.
APP_CARD = re.compile(r"app-card|class=\"card(?![\w-])")
LABEL_TAG = re.compile(r"\.label_tag")
INLINE_WIDTH = re.compile(r"style=\"[^\"]*max-width")


def _lacks_page_header(text):
    return bool(
        EXTENDS_SHELL.search(text) and BLOCK_CONTENT.search(text) and not PAGE_HEADER.search(text)
    )


LEGACY_PAGE_HEAD = {  # 40 files
    "phone_refresh/templates/phone_refresh/api_index.html",
    "phone_refresh/templates/phone_refresh/api_token_created.html",
    "phone_refresh/templates/phone_refresh/api_token_form.html",
    "phone_refresh/templates/phone_refresh/message_form.html",
    "phone_refresh/templates/phone_refresh/providers_index.html",
    "phone_refresh/templates/phone_refresh/reports.html",
    "phone_refresh/templates/phone_refresh/rule_form.html",
    "phone_refresh/templates/phone_refresh/settings_index.html",
    "phone_refresh/templates/phone_refresh/status_form.html",
    "templates/customers/payment_submissions_list.html",
    "templates/employees/my_balance.html",
    "templates/inventory/cards.html",
    "templates/inventory/customer_detail.html",
    "templates/inventory/customer_list.html",
    "templates/inventory/line_detail.html",
    "templates/inventory/main_stock.html",
    "templates/inventory/movements.html",
    "templates/inventory/overview.html",
    "templates/reports/dashboard.html",
    "templates/reports/employee_report.html",
    "templates/reports/phone_lines_report.html",
    "templates/reports/profit_report.html",
    "templates/reports/provider_operations.html",
    "templates/reports/sales_report.html",
    "templates/reports/stale_phone_edit.html",
    "templates/sales/awaiting_approvals.html",
    "templates/sales/employee_entry.html",
    "templates/sales/employee_payment_edit.html",
    "templates/sales/employee_recent.html",
    "templates/sales/employee_record_payment.html",
    "templates/sales/employee_refresh_number.html",
    "templates/sales/employee_sale_edit.html",
    "templates/sales/employee_stock_check.html",
    "templates/sales/management_sale_list.html",
    "templates/sales/pending_payments.html",
    "templates/sales/sale_edit.html",
    "templates/sms_gateway/reports.html",
    "templates/sms_gateway/settings_index.html",
}
LEGACY_APP_CARD = {  # 5 files
    "phone_refresh/templates/phone_refresh/api_token_created.html",
    "phone_refresh/templates/phone_refresh/api_token_form.html",
    "phone_refresh/templates/phone_refresh/message_form.html",
    "phone_refresh/templates/phone_refresh/rule_form.html",
    "phone_refresh/templates/phone_refresh/status_form.html",
}
LEGACY_LABEL_TAG = {  # 15 files
    "phone_refresh/templates/phone_refresh/_general_tab.html",
    "phone_refresh/templates/phone_refresh/_providers_general_tab.html",
    "phone_refresh/templates/phone_refresh/api_token_form.html",
    "phone_refresh/templates/phone_refresh/message_form.html",
    "phone_refresh/templates/phone_refresh/rule_form.html",
    "phone_refresh/templates/phone_refresh/status_form.html",
    "templates/inventory/cards.html",
    "templates/reports/phone_lines_report.html",
    "templates/reports/sales_report.html",
    "templates/reports/stale_phone_edit.html",
    "templates/sales/employee_recent.html",
    "templates/sales/management_sale_list.html",
    "templates/sales/pending_payments.html",
    "templates/sales/sale_edit.html",
}
LEGACY_INLINE_WIDTH = {  # 11 files
    "phone_refresh/templates/phone_refresh/api_token_created.html",
    "phone_refresh/templates/phone_refresh/api_token_form.html",
    "phone_refresh/templates/phone_refresh/message_form.html",
    "phone_refresh/templates/phone_refresh/reports.html",
    "phone_refresh/templates/phone_refresh/rule_form.html",
    "phone_refresh/templates/phone_refresh/status_form.html",
    "templates/core/forbidden.html",
    "templates/reports/stale_phone_edit.html",
    "templates/sales/partials/management_sale_row.html",
    "templates/sales/sale_edit.html",
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
        # `pattern` is a compiled regex or a predicate taking the template source.
        offends = pattern.search if hasattr(pattern, "search") else pattern
        found = {
            name
            for name, text in _template_files()
            if name not in OUT_OF_SCOPE and offends(text)
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

    def test_pages_use_the_kit_page_header(self):
        _, offenders = self._offenders(_lacks_page_header, LEGACY_PAGE_HEAD)
        self._assert_none(
            offenders,
            "Management/employee pages open with the kit header: "
            "{% load kit %} then {% rd_page_header title=... %}...{% end_rd_page_header %}:",
        )

    def test_no_legacy_cards(self):
        _, offenders = self._offenders(APP_CARD, LEGACY_APP_CARD)
        self._assert_none(
            offenders,
            "`app-card` and Bootstrap `.card` are legacy surfaces. Use `.rd-card` "
            "(see DESIGN.md):",
        )

    def test_no_label_tag(self):
        _, offenders = self._offenders(LABEL_TAG, LEGACY_LABEL_TAG)
        self._assert_none(
            offenders,
            "`field.label_tag` renders inconsistent labels. Use {% rd_field form.x %} / "
            "{% rd_fields form %} from the kit:",
        )

    def test_no_inline_max_width(self):
        _, offenders = self._offenders(INLINE_WIDTH, LEGACY_INLINE_WIDTH)
        self._assert_none(
            offenders,
            "Inline `max-width` styles repeat a layout pattern. Use a design-system "
            "class (e.g. a kit/`rd-*` width utility) instead:",
        )

    def test_kit_guard_patterns(self):
        shell = '{% extends "base_management.html" %}{% block content %}x{% endblock %}'
        self.assertTrue(_lacks_page_header(shell))
        self.assertTrue(_lacks_page_header(shell.replace('"base_management.html"', "base_template")))
        self.assertFalse(_lacks_page_header(shell.replace("x", "{% rd_page_header title=t %}{% end_rd_page_header %}")))
        self.assertFalse(_lacks_page_header("{% block content %}x{% endblock %}"))
        self.assertFalse(_lacks_page_header('{% extends "base_management.html" %}{% block title %}t{% endblock %}'))
        for html in ('<div class="app-card p-3">', '<div class="card">', '<div class="card shadow">'):
            self.assertTrue(APP_CARD.search(html), html)
        for html in ('<div class="rd-card">', '<div class="card-body">', '<div class="mb-3 card">'):
            self.assertFalse(APP_CARD.search(html), html)
        self.assertTrue(LABEL_TAG.search("{{ form.name.label_tag }}"))
        self.assertTrue(INLINE_WIDTH.search('<div style="margin:0; max-width: 40rem">'))
        self.assertFalse(INLINE_WIDTH.search('<div style="width: 40rem" class="max-width-x">'))

    # --- Page kit CSS scoping ---------------------------------------------------

    @staticmethod
    def _kit_css_selectors(css):
        """Every selector in the `/* Page kit */` block of design-system.css (comments stripped)."""
        start = css.index("/* Page kit")
        banner = css.index("\n/* ---", start)  # closing rule of the title banner
        end = css.find("\n/* ---", css.index("\n", banner + 1))
        block = re.sub(r"/\*.*?\*/", "", css[start : end if end != -1 else len(css)], flags=re.S)
        selectors = []
        for prelude in re.findall(r"([^{}]+)\{", block):
            prelude = prelude.strip()
            if not prelude or prelude.startswith("@"):
                continue
            depth, current = 0, ""
            for char in prelude:
                depth += (char in "([") - (char in ")]")
                if char == "," and depth == 0:
                    selectors.append(current.strip())
                    current = ""
                else:
                    current += char
            selectors.append(current.strip())
        return [s for s in selectors if s]

    @staticmethod
    def _unscoped_kit_selectors(selectors):
        return [s for s in selectors if not re.search(r"\.rd-[\w-]+", s)]

    def test_kit_css_is_scoped_to_rd_classes(self):
        css = (BASE_DIR / "static/css/design-system.css").read_text(encoding="utf-8")
        selectors = self._kit_css_selectors(css)
        self.assertGreater(len(selectors), 40)  # the parser found the block
        self._assert_none(
            self._unscoped_kit_selectors(selectors),
            "Selectors in the `/* Page kit */` CSS block must contain an `.rd-` class. A bare "
            "element or Bootstrap/Django class (`.form-text`, `.errorlist`, `textarea`, `input`...) "
            "restyles every page that has not migrated; scope it, e.g. `.rd-field textarea.form-control`:",
        )

    def test_kit_css_scope_guard_flags_bare_selectors(self):
        css = "/* Page kit */\n/* ---- */\n.errorlist, .rd-field .form-text, textarea.form-control {a:b}\n@media (x) { .rd-a input {a:b} select {a:b} }\n/* ---- */\n"
        css = "/* ---- */\n" + css
        selectors = self._kit_css_selectors(css)
        self.assertEqual(
            self._unscoped_kit_selectors(selectors), [".errorlist", "textarea.form-control", "select"]
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
            ("LEGACY_PAGE_HEAD", _lacks_page_header, LEGACY_PAGE_HEAD),
            ("LEGACY_APP_CARD", APP_CARD, LEGACY_APP_CARD),
            ("LEGACY_LABEL_TAG", LABEL_TAG, LEGACY_LABEL_TAG),
            ("LEGACY_INLINE_WIDTH", INLINE_WIDTH, LEGACY_INLINE_WIDTH),
        ):
            found, _ = self._offenders(pattern, legacy)
            stale = sorted(legacy - found)
            self._assert_none(
                stale,
                f"These files no longer offend, so remove them from {label} "
                "(the legacy lists may only shrink):",
            )
            self.assertFalse(legacy & OUT_OF_SCOPE, label)
