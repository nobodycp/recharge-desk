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
        self.assertTrue(conn.number_info_enabled)
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
                Capability.NUMBER_INFO: "number_info_enabled",
                Capability.CHARGE: "charge_enabled",
                Capability.RECONCILE: "reconcile_enabled",
            },
        )


class ProviderConnectionMigrationTests(TransactionTestCase):
    # NB: the post-test flush removes migration-seeded rows, so later
    # TransactionTestCases must not rely on them.
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
        # layan: toggle row only -> new connection created.
        ProviderConfig.objects.create(provider="layan", is_enabled=False)
        # sky / areen: toggle row AND an existing connection with credentials.
        ProviderConfig.objects.create(provider="sky", is_enabled=True)
        OldAccount.objects.create(provider_key="sky", username="sky-user")
        ProviderConfig.objects.create(provider="areen", is_enabled=False)
        OldAccount.objects.create(provider_key="areen", username="areen-user")
        # aloha: connection with credentials, NO toggle row -> untouched.
        OldAccount.objects.create(provider_key="aloha", username="aloha-user")
        # "ghost" has neither -> must not get a row.

        new_apps = self._migrate(self.after)
        Connection = new_apps.get_model("providers", "ProviderConnection")

        layan = Connection.objects.get(provider_key="layan")
        self.assertFalse(layan.refresh_enabled)
        self.assertEqual(layan.username, "")

        sky = Connection.objects.get(provider_key="sky")
        self.assertTrue(sky.refresh_enabled)
        self.assertEqual(sky.username, "sky-user")

        areen = Connection.objects.get(provider_key="areen")
        self.assertFalse(areen.refresh_enabled)
        self.assertEqual(areen.username, "areen-user")

        aloha = Connection.objects.get(provider_key="aloha")
        self.assertEqual(aloha.username, "aloha-user")
        self.assertTrue(aloha.is_enabled)
        self.assertTrue(aloha.refresh_enabled)
        self.assertFalse(aloha.charge_enabled)
        self.assertTrue(aloha.reconcile_enabled)

        self.assertFalse(Connection.objects.filter(provider_key="ghost").exists())
        self.assertEqual(
            set(Connection.objects.values_list("provider_key", flat=True)),
            {"layan", "sky", "areen", "aloha"},
        )
