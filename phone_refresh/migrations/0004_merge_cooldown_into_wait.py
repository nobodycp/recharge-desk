"""Merge the duplicate ``COOLDOWN`` status into ``WAIT``.

``WAIT`` (provider-returned "please wait" patterns) and ``COOLDOWN``
(local 6h enforcement) were two names for the same customer-facing
concept ("انتظار"). This migration collapses them:

* Rewrites any historical ``RefreshLog`` rows from ``cooldown`` → ``wait``.
* Updates the ``WAIT`` customer message to the richer cooldown copy
  (with an ``{elapsed}`` placeholder the service interpolates).
* Deletes the now-orphaned ``COOLDOWN`` customer message row.
* Regenerates the ``choices=`` metadata on every field that references
  ``RefreshStatus`` so Django no longer advertises ``cooldown`` in admin
  dropdowns / form widgets.
"""
from django.db import migrations, models


NEW_CHOICES = [
    ("refreshed", "تم التحديث"),
    ("not_found", "غير موجود"),
    ("wait", "انتظار"),
    ("error", "خطأ"),
    ("service_off", "الخدمة متوقفة"),
]

NEW_WAIT_TITLE = "الرجاء الانتظار"
NEW_WAIT_BODY = (
    "لا يمكن تحديث الرقم أكثر من مرة خلال 6 ساعات. "
    "مضى على آخر تحديث: {elapsed}."
)

OLD_WAIT_TITLE = "الرجاء الانتظار"
OLD_WAIT_BODY = "يرجى الانتظار قبل تحديث هذا الرقم مرة أخرى."

OLD_COOLDOWN_TITLE = "الرجاء الانتظار"
OLD_COOLDOWN_BODY = (
    "لا يمكن تحديث الرقم أكثر من مرة خلال 6 ساعات. "
    "مضى على آخر تحديث: {elapsed}."
)


def merge_forward(apps, schema_editor):
    CustomerMessage = apps.get_model("phone_refresh", "CustomerMessage")
    RefreshLog = apps.get_model("phone_refresh", "RefreshLog")
    ProviderResponseRule = apps.get_model("phone_refresh", "ProviderResponseRule")

    # Reassign any historical log/rule rows from cooldown → wait so we
    # don't leave dangling values that fail validation against the new
    # choices set.
    RefreshLog.objects.filter(status="cooldown").update(status="wait")
    ProviderResponseRule.objects.filter(target_status="cooldown").update(
        target_status="wait"
    )

    CustomerMessage.objects.update_or_create(
        status="wait",
        defaults={"title": NEW_WAIT_TITLE, "body": NEW_WAIT_BODY},
    )
    CustomerMessage.objects.filter(status="cooldown").delete()


def merge_reverse(apps, schema_editor):
    CustomerMessage = apps.get_model("phone_refresh", "CustomerMessage")

    # Re-create the old ``cooldown`` row and roll the ``wait`` body back
    # to its pre-merge wording. We can't split historical RefreshLog
    # rows back apart since the original distinction was lost on merge.
    CustomerMessage.objects.update_or_create(
        status="wait",
        defaults={"title": OLD_WAIT_TITLE, "body": OLD_WAIT_BODY},
    )
    CustomerMessage.objects.update_or_create(
        status="cooldown",
        defaults={"title": OLD_COOLDOWN_TITLE, "body": OLD_COOLDOWN_BODY},
    )


class Migration(migrations.Migration):

    dependencies = [
        ("phone_refresh", "0003_general_settings"),
    ]

    operations = [
        migrations.AlterField(
            model_name="customermessage",
            name="status",
            field=models.CharField(choices=NEW_CHOICES, max_length=20, unique=True),
        ),
        migrations.AlterField(
            model_name="providerresponserule",
            name="target_status",
            field=models.CharField(choices=NEW_CHOICES, max_length=20),
        ),
        migrations.AlterField(
            model_name="refreshlog",
            name="status",
            field=models.CharField(choices=NEW_CHOICES, max_length=20),
        ),
        migrations.RunPython(merge_forward, merge_reverse),
    ]
