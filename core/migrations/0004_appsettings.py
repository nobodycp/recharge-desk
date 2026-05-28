from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_sitebranding_site_name_sitebranding_tagline"),
    ]

    operations = [
        migrations.CreateModel(
            name="AppSettings",
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
                (
                    "allow_sales_auto_create_customer",
                    models.BooleanField(
                        default=True,
                        help_text="When off, on-account sales require an existing customer created from the Customers screen.",
                        verbose_name="Allow creating customers from sales entry",
                    ),
                ),
                (
                    "default_language",
                    models.CharField(
                        choices=[("en", "English"), ("ar", "العربية")],
                        default="en",
                        max_length=10,
                        verbose_name="Default language",
                    ),
                ),
                (
                    "default_theme",
                    models.CharField(
                        choices=[
                            ("light", "Light"),
                            ("dark", "Dark"),
                            ("system", "System (match device)"),
                        ],
                        default="system",
                        max_length=10,
                        verbose_name="Default theme",
                    ),
                ),
                (
                    "public_default_language",
                    models.CharField(
                        choices=[("en", "English"), ("ar", "العربية")],
                        default="ar",
                        max_length=10,
                        verbose_name="Public refresh page default language",
                    ),
                ),
                (
                    "public_default_theme",
                    models.CharField(
                        choices=[("light", "Light"), ("dark", "Dark")],
                        default="dark",
                        max_length=10,
                        verbose_name="Public refresh page default theme",
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="updated at"),
                ),
            ],
            options={
                "verbose_name": "system settings",
                "verbose_name_plural": "system settings",
            },
        ),
    ]
