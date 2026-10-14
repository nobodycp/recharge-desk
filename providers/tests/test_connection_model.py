from __future__ import annotations

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase

from providers.contracts import Capability
from providers.models import ProviderConnection


class ProviderConnectionModelTests(TestCase):
    def setUp(self):
        ProviderConnection.objects.all().delete()

    def test_for_key_creates_enabled_row_with_defaults(self):
        conn = ProviderConnection.for_key("aloha")

        self.assertTrue(conn.is_enabled)
        self.assertTrue(conn.refresh_enabled)
        self.assertFalse(conn.charge_enabled)
        self.assertTrue(conn.reconcile_enabled)
        self.assertEqual(ProviderConnection.for_key("aloha").pk, conn.pk)
        self.assertEqual(ProviderConnection.objects.count(), 1)

    def test_table_name_unchanged(self):
        self.assertEqual(ProviderConnection._meta.db_table, "providers_provideraccount")

    def test_service_fields_map(self):
        self.assertEqual(
            ProviderConnection.SERVICE_FIELDS,
            {
                Capability.REFRESH: "refresh_enabled",
                Capability.CHARGE: "charge_enabled",
                Capability.RECONCILE: "reconcile_enabled",
            },
        )


class ProviderConnectionMigrationTests(TransactionTestCase):
    before = [
        ("providers", "0003_providerjob"),
        ("phone_refresh", "0020_apitoken_scopes"),
    ]
    after = [("providers", "0004_provider_connection")]

    def _migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_migration_copies_refresh_toggle(self):
        old_apps = self._migrate(self.before)
        ProviderConfig = old_apps.get_model("phone_refresh", "ProviderConfig")
        OldAccount = old_apps.get_model("providers", "ProviderAccount")
        OldAccount.objects.all().delete()
        ProviderConfig.objects.all().delete()
        ProviderConfig.objects.create(provider="layan", is_enabled=False)
        ProviderConfig.objects.create(provider="sky", is_enabled=True)

        new_apps = self._migrate(self.after)
        Connection = new_apps.get_model("providers", "ProviderConnection")

        self.assertFalse(Connection.objects.get(provider_key="layan").refresh_enabled)
        self.assertTrue(Connection.objects.get(provider_key="sky").refresh_enabled)
        self.assertEqual(
            set(Connection.objects.values_list("provider_key", flat=True)),
            {"layan", "sky"},
        )
