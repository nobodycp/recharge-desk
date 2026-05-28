from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0005_appsettings_workflow_and_sales_ui"),
    ]

    operations = [
        migrations.AddField(
            model_name="appsettings",
            name="sales_show_employee_payment",
            field=models.BooleanField(
                default=True,
                verbose_name="Show payment to employee on sales entry",
            ),
        ),
    ]
