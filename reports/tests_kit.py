from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


REPORT_TEMPLATES = (
    "dashboard.html",
    "employee_report.html",
    "phone_lines_report.html",
    "profit_report.html",
    "provider_operations.html",
    "sales_report.html",
    "stale_phone_edit.html",
)


class ReportsPageKitTests(SimpleTestCase):
    def _source(self, name):
        return (
            Path(settings.BASE_DIR) / "templates" / "reports" / name
        ).read_text(encoding="utf-8")

    def test_report_pages_use_the_page_kit(self):
        for name in REPORT_TEMPLATES:
            with self.subTest(name=name):
                source = self._source(name)
                self.assertIn("{% load", source)
                self.assertIn(" kit", source)
                self.assertIn('class="rd-page', source)
                self.assertIn("{% rd_page_header", source)

    def test_dashboard_keeps_period_switch_htmx_contract(self):
        source = self._source("dashboard.html")
        self.assertIn('id="rd-dashboard"', source)
        self.assertIn("rd-period-switch", source)
        self.assertIn('hx-target="#rd-dashboard"', source)
        self.assertIn('hx-select="#rd-dashboard"', source)
        self.assertIn('hx-swap="outerHTML"', source)
        self.assertIn('hx-push-url="true"', source)

    def test_dashboard_swap_keeps_page_header_interactive(self):
        css = (Path(settings.BASE_DIR) / "static" / "css" / "app.css").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            ".rd-dash-swap.htmx-request > :not(.rd-page-head)",
            css,
        )

    def test_report_forms_use_kit_fields_without_renaming_inputs(self):
        phone_lines = self._source("phone_lines_report.html")
        self.assertIn('id="phone-lines-filter-form"', phone_lines)
        self.assertIn("{% rd_field filter_form.q", phone_lines)
        for name in ("sort", "order"):
            self.assertIn(f'name="{name}"', phone_lines)

        sales = self._source("sales_report.html")
        self.assertIn("{% rd_field form.company", sales)
        for name in ("q", "sort", "order"):
            self.assertIn(f'name="{name}"', sales)

        stale_edit = self._source("stale_phone_edit.html")
        self.assertIn("{% rd_fields form", stale_edit)
        self.assertNotIn(".label_tag", stale_edit)
        self.assertNotIn('style="max-width', stale_edit)
