"""Regression: phone_refresh singleton settings must survive migrate re-runs."""
from importlib import import_module

from django.apps import apps
from django.test import TestCase

from phone_refresh.models import ApiSettings


class SingletonSeedIdempotencyTests(TestCase):
    """Data migrations must not clobber admin-edited singleton rows."""

    def test_api_settings_seed_preserves_existing_row(self):
        ApiSettings.objects.filter(pk=1).update(
            require_token=True,
            rate_limit_per_minute=5,
            rate_limit_per_hour=50,
            allow_anonymous_test_page=False,
            allowed_origins="https://example.com",
        )

        mod = import_module("phone_refresh.migrations.0006_api_management")
        mod.seed_api_settings(apps, schema_editor=None)

        row = ApiSettings.objects.get(pk=1)
        self.assertTrue(row.require_token)
        self.assertEqual(row.rate_limit_per_minute, 5)
        self.assertEqual(row.rate_limit_per_hour, 50)
        self.assertFalse(row.allow_anonymous_test_page)
        self.assertEqual(row.allowed_origins, "https://example.com")

    def test_migrate_does_not_reset_customized_api_settings(self):
        ApiSettings.objects.update_or_create(
            pk=1,
            defaults={
                "require_token": True,
                "rate_limit_per_minute": 12,
                "rate_limit_per_hour": 120,
                "allow_anonymous_test_page": False,
                "allowed_origins": "https://client.example",
            },
        )

        from django.core.management import call_command

        call_command("migrate", "phone_refresh", verbosity=0)

        row = ApiSettings.objects.get(pk=1)
        self.assertTrue(row.require_token)
        self.assertEqual(row.rate_limit_per_minute, 12)
        self.assertEqual(row.rate_limit_per_hour, 120)
        self.assertEqual(row.allowed_origins, "https://client.example")
