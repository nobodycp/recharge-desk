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
