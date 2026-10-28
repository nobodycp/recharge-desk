"""Undo the 0020 backfill where it subtracted a chip the sale never recorded.

0020 assumed every new-SIM ``cost_price_snapshot`` was package + chip, then
cut ``estimated_unit_cost`` out of the deduction and credited that gap to the
company balance. Most historical snapshots are the package price alone, so
the cut removed a real supplier charge (and, when the package was not larger
than the chip, zeroed the deduction). This puts those deductions back to the
supplier package and takes the extra credit off the balance.

A snapshot that really is package + chip stays at the supplier-only amount
0020 (or ``create_sale``) already stored. Cancelled sales stay untouched:
their deduction and reversal already net to zero.
"""

from decimal import Decimal

from django.db import migrations
from django.db.models import F


def repair_new_sim_provider_balance(apps, schema_editor):
    from sales.pricing import provider_comparable_cost

    Sale = apps.get_model("sales", "Sale")
    CompanyBalanceTransaction = apps.get_model("sales", "CompanyBalanceTransaction")
    Company = apps.get_model("companies", "Company")

    CANCELLED = "cancelled"
    DEDUCTION = "deduction"
    SALE_REF = "sale"

    sales = (
        Sale.objects.filter(is_new_sim=True)
        .exclude(status=CANCELLED)
        .select_related("product__line")
    )
    sale_ids = list(sales.values_list("pk", flat=True))
    if not sale_ids:
        return

    txns_by_sale = {
        t.reference_id: t
        for t in CompanyBalanceTransaction.objects.filter(
            entry_type=DEDUCTION,
            reference_type=SALE_REF,
            reference_id__in=sale_ids,
        )
    }

    for sale in sales:
        txn = txns_by_sale.get(sale.pk)
        if txn is None:
            continue
        correct = provider_comparable_cost(sale)
        current = Decimal(txn.amount)
        # Positive means the deduction is too small and the balance is too high.
        shortfall = correct - current
        if shortfall == 0:
            continue
        CompanyBalanceTransaction.objects.filter(pk=txn.pk).update(amount=correct)
        Company.objects.filter(pk=sale.company_id).update(
            current_balance=F("current_balance") - shortfall
        )


class Migration(migrations.Migration):
    dependencies = [
        ("sales", "0020_backfill_new_sim_provider_balance"),
    ]

    operations = [
        migrations.RunPython(
            repair_new_sim_provider_balance, migrations.RunPython.noop
        ),
    ]
