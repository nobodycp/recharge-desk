"""Convert legacy "Not paid" sales to the new on-account / awaiting flow.

Any sale whose payment_method matches a "Not paid" / "NotPaid" / "غير مدفوع"
spelling is rewritten as ``on_account=True``, ``payment_method=NULL``, and
``status=AWAITING`` so management can explicitly approve each historical
record before it lands on a customer's tab. The matching PaymentMethod row
is then deactivated (kept for audit history rather than deleted).

Reverse: best-effort restoration that re-enables the deactivated method
and points the migrated sales back at it. Status / on_account are not
restored automatically because we'd otherwise risk corrupting customer
ledger entries created in the meantime.
"""

from django.db import migrations


NOT_PAID_NEEDLES = ("not paid", "notpaid", "not-paid", "غير مدفوع", "لم يدفع")


def _find_notpaid_method(PaymentMethod):
    for needle in NOT_PAID_NEEDLES:
        pm = PaymentMethod.objects.filter(name__iexact=needle).first()
        if pm:
            return pm
    for needle in NOT_PAID_NEEDLES:
        pm = PaymentMethod.objects.filter(name__icontains=needle).first()
        if pm:
            return pm
    return None


def forwards(apps, schema_editor):
    PaymentMethod = apps.get_model("sales", "PaymentMethod")
    Sale = apps.get_model("sales", "Sale")

    pm = _find_notpaid_method(PaymentMethod)
    if not pm:
        return

    Sale.objects.filter(payment_method=pm).update(
        payment_method=None,
        on_account=True,
        status="awaiting",
    )

    if pm.is_active:
        pm.is_active = False
        pm.save(update_fields=["is_active"])


def backwards(apps, schema_editor):
    PaymentMethod = apps.get_model("sales", "PaymentMethod")
    Sale = apps.get_model("sales", "Sale")

    pm = _find_notpaid_method(PaymentMethod)
    if not pm:
        return

    if not pm.is_active:
        pm.is_active = True
        pm.save(update_fields=["is_active"])

    Sale.objects.filter(payment_method__isnull=True, on_account=True).update(
        payment_method=pm,
        on_account=False,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0008_customer_accounts_initial"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
