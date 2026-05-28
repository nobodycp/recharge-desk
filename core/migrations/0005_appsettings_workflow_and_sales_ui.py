from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0004_appsettings"),
    ]

    operations = [
        migrations.AddField(
            model_name="appsettings",
            name="require_debt_request_approval",
            field=models.BooleanField(
                default=True,
                help_text="When off, on-account sales post to the customer immediately without awaiting management approval.",
                verbose_name="Require approval for debt requests",
            ),
        ),
        migrations.AddField(
            model_name="appsettings",
            name="require_settlement_request_approval",
            field=models.BooleanField(
                default=True,
                help_text="When off, customer settlement submissions apply to the balance immediately without management approval.",
                verbose_name="Require approval for settlement requests",
            ),
        ),
        migrations.AddField(
            model_name="appsettings",
            name="require_payment_request_approval",
            field=models.BooleanField(
                default=True,
                help_text="When off, cash sales are marked paid immediately without appearing in pending payments.",
                verbose_name="Require approval for payment requests",
            ),
        ),
        migrations.AddField(
            model_name="appsettings",
            name="sales_inventory_enabled",
            field=models.BooleanField(
                default=True,
                help_text="When off, the New SIM option is hidden on the employee sales screen. Inventory management elsewhere is unchanged.",
                verbose_name="Show inventory (New SIM) on sales entry",
            ),
        ),
        migrations.AddField(
            model_name="appsettings",
            name="sales_show_refresh_phone",
            field=models.BooleanField(
                default=True,
                verbose_name="Show phone refresh on sales entry",
            ),
        ),
        migrations.AddField(
            model_name="appsettings",
            name="sales_show_record_payment",
            field=models.BooleanField(
                default=True,
                verbose_name="Show record payment on sales entry",
            ),
        ),
    ]
