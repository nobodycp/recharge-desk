import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0003_productline_refactor"),
    ]

    operations = [
        migrations.AddField(
            model_name="productline",
            name="default_package",
            field=models.ForeignKey(
                blank=True,
                help_text="If this line has several packages, employees see this one selected first; they can still pick another.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="companies.product",
                verbose_name="default package for sales",
            ),
        ),
    ]
