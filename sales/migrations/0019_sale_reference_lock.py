from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0018_sale_charge_conflict"),
    ]

    operations = [
        migrations.CreateModel(
            name="SaleReferenceLock",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("reference_key", models.CharField(max_length=64, unique=True)),
            ],
            options={
                "db_table": "sales_reference_lock",
            },
        ),
    ]
