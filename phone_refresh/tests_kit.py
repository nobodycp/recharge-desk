from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


PAGE_TEMPLATES = (
    "api_index.html",
    "api_token_created.html",
    "api_token_form.html",
    "message_form.html",
    "providers_index.html",
    "reports.html",
    "rule_form.html",
    "settings_index.html",
    "status_form.html",
)

FORM_TEMPLATES = (
    "api_token_form.html",
    "message_form.html",
    "rule_form.html",
    "status_form.html",
)


class PhoneRefreshPageKitTests(SimpleTestCase):
    def _source(self, name):
        return (
            Path(settings.BASE_DIR)
            / "phone_refresh"
            / "templates"
            / "phone_refresh"
            / name
        ).read_text(encoding="utf-8")

    def test_admin_pages_use_page_kit_headers_without_legacy_surfaces(self):
        for name in PAGE_TEMPLATES:
            with self.subTest(name=name):
                source = self._source(name)
                self.assertIn("{% load kit %}", source)
                self.assertIn('class="rd-page', source)
                self.assertIn("{% rd_page_header", source)
                self.assertNotIn("app-card", source)
                self.assertNotIn('class="card', source)
                self.assertNotIn('style="max-width', source)

    def test_edit_forms_use_kit_fields_and_actions(self):
        for name in FORM_TEMPLATES:
            with self.subTest(name=name):
                source = self._source(name)
                self.assertIn("{% rd_fields form %}", source)
                self.assertIn("rd-form-actions", source)
                self.assertNotIn(".label_tag", source)

    def test_settings_tabs_keep_post_contracts_and_use_kit_fields(self):
        general = self._source("_general_tab.html")
        self.assertIn("{% load kit %}", general)
        self.assertIn("{% rd_field internal_test_form.phone %}", general)
        self.assertIn(
            "{% rd_field settings_form.default_provider ",
            general,
        )
        self.assertIn(
            'action="{% url \'phone_refresh:settings_general_save\' %}"',
            general,
        )

        providers = self._source("_providers_general_tab.html")
        self.assertIn("{% load kit %}", providers)
        self.assertIn("{% rd_field provider_test_form.phone %}", providers)
        self.assertIn("{% rd_field provider_test_form.provider %}", providers)
        self.assertIn(
            'action="{% url \'phone_refresh:providers_test\' %}"',
            providers,
        )

    def test_api_token_secret_contract_stays_one_time_only(self):
        create_source = self._source("api_token_form.html")
        self.assertNotIn("raw_token", create_source)

        created_source = self._source("api_token_created.html")
        self.assertIn('value="{{ raw_token }}"', created_source)
        self.assertIn("لن يظهر مرة أخرى", created_source)

    def test_reports_bulk_toolbar_rule_is_well_formed(self):
        source = self._source("reports.html")
        self.assertIn(".pr-report-excerpt { max-width: 28rem; }", source)
        self.assertIn("data-rd-select", source)
        self.assertNotIn("pr-bulk-toolbar", source)
