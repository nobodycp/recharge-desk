# ProductLine + Product as variant under line (data migrated from old Product rows)

import django.db.models.deletion
from django.db import migrations, models


def forwards_fill_product_lines(apps, schema_editor):
    Product = apps.get_model("companies", "Product")
    ProductLine = apps.get_model("companies", "ProductLine")
    for p in Product.objects.all():
        line = ProductLine.objects.filter(company_id=p.company_id, name=p.name).first()
        if not line:
            line = ProductLine.objects.create(
                company_id=p.company_id,
                name=p.name,
                is_active=p.is_active,
                sort_order=0,
            )
        p.line_id = line.id
        p.variant_label = ""
        p.save(update_fields=["line_id", "variant_label"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0002_company_icon_product_icon"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProductLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=200, verbose_name="product line")),
                (
                    "icon",
                    models.ImageField(
                        blank=True,
                        help_text="Default icon for all packages in this line; a package can override.",
                        null=True,
                        upload_to="icons/product_lines/",
                        verbose_name="line icon",
                    ),
                ),
                ("sort_order", models.PositiveSmallIntegerField(default=0, verbose_name="sort order")),
                ("is_active", models.BooleanField(default=True, verbose_name="active")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="product_lines",
                        to="companies.company",
                        verbose_name="company",
                    ),
                ),
            ],
            options={
                "verbose_name": "product line",
                "verbose_name_plural": "product lines",
                "ordering": ["company", "sort_order", "name"],
            },
        ),
        migrations.AddField(
            model_name="product",
            name="line",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="variants",
                to="companies.productline",
                verbose_name="product line",
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="variant_label",
            field=models.CharField(
                blank=True,
                default="",
                help_text='Examples: "100 GB", "200 GB". Leave empty if the line has only one package.',
                max_length=120,
                verbose_name="package",
            ),
        ),
        migrations.RunPython(forwards_fill_product_lines, noop_reverse),
        migrations.RemoveField(model_name="product", name="company"),
        migrations.RemoveField(model_name="product", name="name"),
        migrations.AlterField(
            model_name="product",
            name="line",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="variants",
                to="companies.productline",
                verbose_name="product line",
            ),
        ),
        migrations.AlterModelOptions(
            name="product",
            options={
                "ordering": ["line__company", "line__sort_order", "line__name", "variant_label"],
                "verbose_name": "product package",
                "verbose_name_plural": "product packages",
            },
        ),
        migrations.AddConstraint(
            model_name="productline",
            constraint=models.UniqueConstraint(fields=("company", "name"), name="uniq_company_productline_name"),
        ),
    ]
