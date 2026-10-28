"""Data fix: a new-SIM sale's chip cost was wrongly deducted from the
supplier's balance (that chip is our own stock, never the supplier's — see
``sales.pricing.provider_comparable_cost``). This corrects every still-open
sale's recorded deduction down to the supplier-only amount and credits the
difference back to the company balance, so historical ledgers match the
fixed ``create_sale`` / ``cancel_sale`` behaviour going forward.

Cancelled sales are left untouched: their deduction and cancellation
reversal both used the old (inflated) amount, so the net effect on the
company balance was already zero.
"""

from decimal import Decimal

from django.db import migrations
from django.db.models import F


def backfill_new_sim_provider_balance(apps, schema_editor):
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
        raw_chip = getattr(sale.product.line, "estimated_unit_cost", None)
        chip = Decimal(raw_chip) if raw_chip is not None else Decimal("0")
        if chip <= 0:
            continue
        # The target amount is derived from the immutable cost snapshot, not
        # from the (possibly already-corrected) transaction amount, so a
        # second run of this migration is a no-op instead of subtracting the
        # chip cost again.
        full_cost = Decimal(sale.cost_price_snapshot)
        target = full_cost - chip if full_cost > chip else Decimal("0")
        current_amount = Decimal(txn.amount)
        overcharge = current_amount - target
        if overcharge <= 0:
            continue
        CompanyBalanceTransaction.objects.filter(pk=txn.pk).update(amount=target)
        Company.objects.filter(pk=sale.company_id).update(
            current_balance=F("current_balance") + overcharge
        )


class Migration(migrations.Migration):
    dependencies = [
        ("sales", "0019_sale_reference_lock"),
        ("companies", "0008_productline_estimated_unit_cost_help_text"),
    ]

    operations = [
        migrations.RunPython(
            backfill_new_sim_provider_balance, migrations.RunPython.noop
        ),
    ]
