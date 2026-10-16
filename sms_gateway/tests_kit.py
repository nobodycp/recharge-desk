from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from sms_gateway.forms import SmsReportFilterForm


class SmsGatewayPageKitTests(SimpleTestCase):
    def _source(self, name):
        return (
            Path(settings.BASE_DIR) / "templates" / "sms_gateway" / name
        ).read_text(encoding="utf-8")

    def test_pages_use_page_kit_headers(self):
        for name in ("reports.html", "settings_index.html"):
            with self.subTest(name=name):
                source = self._source(name)
                self.assertIn("{% load kit %}", source)
                self.assertIn('class="rd-page', source)
                self.assertIn("{% rd_page_header", source)

    def test_reports_filter_uses_kit_and_keeps_get_contract(self):
        source = self._source("reports.html")
        self.assertIn("{% rd_filter_card report_filter_form", source)
        self.assertEqual(tuple(SmsReportFilterForm().fields), ("q", "state"))

    def test_settings_forms_use_kit_fields_and_keep_post_contracts(self):
        source = self._source("settings_index.html")
        self.assertIn("rd-form-grid", source)
        self.assertNotIn("rd-field-grid", source)
        for form_name in (
            "general_form",
            "api_form",
            "device_form",
            "access_rule_form",
        ):
            self.assertIn(f"{{% rd_field {form_name}.", source)
        for url_name in (
            "settings_general_save",
            "api_gateway_save",
            "device_create",
            "reply_policy_save",
            "access_rule_create",
        ):
            self.assertIn(f"sms_gateway:{url_name}", source)

    def test_migrated_templates_have_no_legacy_colors_or_badges(self):
        settings_source = self._source("settings_index.html")
        self.assertNotRegex(settings_source, r"#[0-9a-fA-F]{6}\b")

        serials_source = (
            Path(settings.BASE_DIR)
            / "templates"
            / "inventory"
            / "_serials_paste_field.html"
        ).read_text(encoding="utf-8")
        self.assertIn("rd-badge rd-badge--neutral", serials_source)
        self.assertNotIn("text-bg-", serials_source)


class SmsGatewayReportsRenderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = get_user_model().objects.create_superuser(
            "sms_reports_manager",
            "sms-reports@example.com",
            "x",
        )

    def test_reports_bare_get_renders(self):
        self.client.force_login(self.manager)

        response = self.client.get(reverse("sms_gateway:reports"))

        self.assertEqual(response.status_code, 200)
