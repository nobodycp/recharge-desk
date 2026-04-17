# Generated manually: payment icons + drop unused customer_name

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="paymentmethod",
            name="icon",
            field=models.ImageField(
                blank=True,
                help_text="Shown on the employee sales screen instead of the method name.",
                null=True,
                upload_to="icons/payment_methods/",
                verbose_name="icon",
            ),
        ),
        migrations.RemoveField(
            model_name="sale",
            name="customer_name",
        ),
    ]
