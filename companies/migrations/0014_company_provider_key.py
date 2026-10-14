from django.db import migrations, models


class Migration(migrations.Migration):
    """Rename ``Company.phone_refresh_provider`` -> ``provider_key``.

    The DB column keeps its name: the field is first pinned to the legacy column
    with ``db_column``, then renamed, so no column is altered.
    """

    dependencies = [
        ("companies", "0013_phone_refresh_provider_help_text"),
    ]

    operations = [
        migrations.AlterField(
            model_name="company",
            name="phone_refresh_provider",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "Not set"),
                    ("sky", "Sky"),
                    ("areen", "Areen"),
                    ("layan", "Layan"),
                    ("aloha", "Aloha"),
                ],
                db_column="phone_refresh_provider",
                help_text=(
                    "Which upstream system runs refresh, charge and reconciliation"
                    " for this company. Choose it explicitly: if left empty, this"
                    " company has no provider routing and no provider operations."
                ),
                max_length=20,
                verbose_name="company system",
            ),
        ),
        migrations.RenameField(
            model_name="company",
            old_name="phone_refresh_provider",
            new_name="provider_key",
        ),
    ]
