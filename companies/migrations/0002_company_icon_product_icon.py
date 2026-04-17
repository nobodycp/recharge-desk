# Generated manually for icon uploads

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="company",
            name="icon",
            field=models.ImageField(
                blank=True,
                help_text="Shown on the employee sales screen instead of the name.",
                null=True,
                upload_to="icons/companies/",
                verbose_name="icon",
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="icon",
            field=models.ImageField(
                blank=True,
                help_text="Shown on the employee sales screen instead of the product name.",
                null=True,
                upload_to="icons/products/",
                verbose_name="icon",
            ),
        ),
    ]
