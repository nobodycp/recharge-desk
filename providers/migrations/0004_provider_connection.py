from django.db import migrations, models


def copy_refresh_toggles(apps, schema_editor):
    """Carry the old per-provider refresh toggle over to the connection row."""
    ProviderConfig = apps.get_model("phone_refresh", "ProviderConfig")
    ProviderConnection = apps.get_model("providers", "ProviderConnection")
    for row in ProviderConfig.objects.all():
        connection, _ = ProviderConnection.objects.get_or_create(
            provider_key=row.provider
        )
        connection.refresh_enabled = row.is_enabled
        connection.save(update_fields=["refresh_enabled"])


class Migration(migrations.Migration):

    dependencies = [
        ("providers", "0003_providerjob"),
        ("phone_refresh", "0020_apitoken_scopes"),
    ]

    operations = [
        migrations.AlterModelTable(
            name="provideraccount",
            table="providers_provideraccount",
        ),
        migrations.RenameModel(
            old_name="ProviderAccount",
            new_name="ProviderConnection",
        ),
        migrations.AddField(
            model_name="providerconnection",
            name="refresh_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="providerconnection",
            name="charge_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="providerconnection",
            name="reconcile_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(copy_refresh_toggles, migrations.RunPython.noop),
    ]
