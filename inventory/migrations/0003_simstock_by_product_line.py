from collections import defaultdict

from django.db import migrations, models
import django.db.models.deletion


def company_to_product_line(apps, schema_editor):
    SimStockBalance = apps.get_model("inventory", "SimStockBalance")
    SimStockMovement = apps.get_model("inventory", "SimStockMovement")
    ProductLine = apps.get_model("companies", "ProductLine")

    company_to_line: dict[int, int] = {}
    for line in ProductLine.objects.all().order_by("company_id", "pk"):
        if line.company_id not in company_to_line:
            company_to_line[line.company_id] = line.pk

    for balance in SimStockBalance.objects.all().iterator():
        line_id = company_to_line.get(balance.company_id)
        if line_id:
            balance.product_line_id = line_id
            balance.save(update_fields=["product_line_id"])

    for movement in SimStockMovement.objects.all().iterator():
        line_id = company_to_line.get(movement.company_id)
        if line_id:
            movement.product_line_id = line_id
            movement.save(update_fields=["product_line_id"])

    # Merge duplicate balances that share the same product-line name (shared Sleekom pool).
    lines_by_name: dict[str, list] = defaultdict(list)
    for line in ProductLine.objects.all().only("pk", "name"):
        key = (line.name or "").strip().casefold()
        if key:
            lines_by_name[key].append(line.pk)

    canonical: dict[int, int] = {}
    for _name, pks in lines_by_name.items():
        winner = min(pks)
        for pk in pks:
            canonical[pk] = winner

    for balance in SimStockBalance.objects.all().iterator():
        winner = canonical.get(balance.product_line_id, balance.product_line_id)
        if winner != balance.product_line_id:
            balance.product_line_id = winner
            balance.save(update_fields=["product_line_id"])

    groups: dict[tuple, list] = defaultdict(list)
    for balance in SimStockBalance.objects.all().iterator():
        groups[(balance.location, balance.product_line_id, balance.customer_id)].append(balance)

    for balances in groups.values():
        if len(balances) <= 1:
            continue
        primary = balances[0]
        primary.quantity = sum(b.quantity for b in balances)
        primary.save(update_fields=["quantity"])
        for extra in balances[1:]:
            extra.delete()

    for movement in SimStockMovement.objects.all().iterator():
        winner = canonical.get(movement.product_line_id, movement.product_line_id)
        if winner != movement.product_line_id:
            movement.product_line_id = winner
            movement.save(update_fields=["product_line_id"])


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0002_simstock_by_company"),
    ]

    operations = [
        migrations.AddField(
            model_name="simstockbalance",
            name="product_line",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="sim_stock_balances",
                to="companies.productline",
                verbose_name="product line",
            ),
        ),
        migrations.AddField(
            model_name="simstockmovement",
            name="product_line",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="sim_stock_movements",
                to="companies.productline",
                verbose_name="product line",
            ),
        ),
        migrations.RunPython(company_to_product_line, migrations.RunPython.noop),
        migrations.RemoveIndex(
            model_name="simstockmovement",
            name="sim_mov_company_created_idx",
        ),
        migrations.RemoveConstraint(
            model_name="simstockbalance",
            name="sim_balance_unique_main_per_company",
        ),
        migrations.RemoveConstraint(
            model_name="simstockbalance",
            name="sim_balance_unique_customer_per_company",
        ),
        migrations.RemoveField(
            model_name="simstockbalance",
            name="company",
        ),
        migrations.RemoveField(
            model_name="simstockmovement",
            name="company",
        ),
        migrations.AlterField(
            model_name="simstockbalance",
            name="product_line",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="sim_stock_balances",
                to="companies.productline",
                verbose_name="product line",
            ),
        ),
        migrations.AlterField(
            model_name="simstockmovement",
            name="product_line",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="sim_stock_movements",
                to="companies.productline",
                verbose_name="product line",
            ),
        ),
        migrations.AddIndex(
            model_name="simstockmovement",
            index=models.Index(fields=["product_line", "-created_at"], name="sim_mov_line_created_idx"),
        ),
        migrations.AddConstraint(
            model_name="simstockbalance",
            constraint=models.UniqueConstraint(
                condition=models.Q(("location", "main")),
                fields=("product_line",),
                name="sim_balance_unique_main_per_line",
            ),
        ),
        migrations.AddConstraint(
            model_name="simstockbalance",
            constraint=models.UniqueConstraint(
                condition=models.Q(("location", "customer")),
                fields=("customer", "product_line"),
                name="sim_balance_unique_customer_per_line",
            ),
        ),
        migrations.AlterModelOptions(
            name="simstockbalance",
            options={
                "ordering": ["product_line__name", "customer__name"],
                "verbose_name": "SIM stock balance",
                "verbose_name_plural": "SIM stock balances",
            },
        ),
    ]
