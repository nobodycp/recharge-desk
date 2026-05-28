from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0005_alter_product_icon_alter_product_variant_label"),
    ]

    operations = [
        migrations.AddField(
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
                help_text=(
                    "Which upstream API runs phone refresh for this company."
                    " Leave empty to match from the company name (legacy)."
                ),
                max_length=20,
                verbose_name="phone refresh provider",
            ),
        ),
    ]
