from collections import defaultdict

from django.db import migrations, models
import django.db.models.deletion


def copy_product_line_to_company(apps, schema_editor):
    SimStockBalance = apps.get_model("inventory", "SimStockBalance")
    SimStockMovement = apps.get_model("inventory", "SimStockMovement")
    ProductLine = apps.get_model("companies", "ProductLine")

    line_to_company = {
        row.pk: row.company_id for row in ProductLine.objects.all().only("pk", "company_id")
    }

    for balance in SimStockBalance.objects.all().iterator():
        company_id = line_to_company.get(balance.product_line_id)
        if company_id:
            balance.company_id = company_id
            balance.save(update_fields=["company_id"])

    for movement in SimStockMovement.objects.all().iterator():
        company_id = line_to_company.get(movement.product_line_id)
        if company_id:
            movement.company_id = company_id
            movement.save(update_fields=["company_id"])

    groups: dict[tuple, list] = defaultdict(list)
    for balance in SimStockBalance.objects.all().iterator():
        groups[(balance.location, balance.company_id, balance.customer_id)].append(balance)

    for balances in groups.values():
        if len(balances) <= 1:
            continue
        primary = balances[0]
        primary.quantity = sum(b.quantity for b in balances)
        primary.save(update_fields=["quantity"])
        for extra in balances[1:]:
            extra.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0001_initial"),
        ("companies", "0006_company_phone_refresh_provider"),
    ]

    operations = [
        migrations.AddField(
            model_name="simstockbalance",
            name="company",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="sim_stock_balances",
                to="companies.company",
                verbose_name="company",
            ),
        ),
        migrations.AddField(
            model_name="simstockmovement",
            name="company",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="sim_stock_movements",
                to="companies.company",
                verbose_name="company",
            ),
        ),
        migrations.RunPython(copy_product_line_to_company, migrations.RunPython.noop),
        migrations.RemoveIndex(
            model_name="simstockmovement",
            name="sim_mov_line_created_idx",
        ),
        migrations.RemoveConstraint(
            model_name="simstockbalance",
            name="sim_balance_unique_main_per_line",
        ),
        migrations.RemoveConstraint(
            model_name="simstockbalance",
            name="sim_balance_unique_customer_per_line",
        ),
        migrations.RemoveField(
            model_name="simstockbalance",
            name="product_line",
        ),
        migrations.RemoveField(
            model_name="simstockmovement",
            name="product_line",
        ),
        migrations.AlterField(
            model_name="simstockbalance",
            name="company",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="sim_stock_balances",
                to="companies.company",
                verbose_name="company",
            ),
        ),
        migrations.AlterField(
            model_name="simstockmovement",
            name="company",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="sim_stock_movements",
                to="companies.company",
                verbose_name="company",
            ),
        ),
        migrations.AddIndex(
            model_name="simstockmovement",
            index=models.Index(fields=["company", "-created_at"], name="sim_mov_company_created_idx"),
        ),
        migrations.AddConstraint(
            model_name="simstockbalance",
            constraint=models.UniqueConstraint(
                condition=models.Q(("location", "main")),
                fields=("company",),
                name="sim_balance_unique_main_per_company",
            ),
        ),
        migrations.AddConstraint(
            model_name="simstockbalance",
            constraint=models.UniqueConstraint(
                condition=models.Q(("location", "customer")),
                fields=("customer", "company"),
                name="sim_balance_unique_customer_per_company",
            ),
        ),
        migrations.AlterModelOptions(
            name="simstockbalance",
            options={
                "ordering": ["company__name", "customer__name"],
                "verbose_name": "SIM stock balance",
                "verbose_name_plural": "SIM stock balances",
            },
        ),
    ]
