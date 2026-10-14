"""Add ``refresh_wait_seconds`` and seed the ``processing`` / ``busy`` statuses.

Existing statuses and customer messages are never overwritten: an admin may
already have created or edited rows with these codes.
"""
from django.db import migrations, models

NEW_STATUSES = [
    # (code, label, sort_order, message title, message body)
    (
        "processing",
        "قيد المعالجة",
        70,
        "قيد المعالجة",
        "طلبك قيد المعالجة، أعد المحاولة بعد دقيقة.",
    ),
    (
        "busy",
        "الخدمة مشغولة",
        80,
        "الخدمة مشغولة",
        "الخدمة مشغولة حالياً، حاول بعد قليل.",
    ),
]


def seed_forward(apps, schema_editor):
    RefreshStatus = apps.get_model("phone_refresh", "RefreshStatus")
    CustomerMessage = apps.get_model("phone_refresh", "CustomerMessage")
    for code, label, sort_order, title, body in NEW_STATUSES:
        status, _ = RefreshStatus.objects.get_or_create(
            code=code,
            defaults={"label": label, "is_system": True, "sort_order": sort_order},
        )
        CustomerMessage.objects.get_or_create(
            status=status, defaults={"title": title, "body": body}
        )


def seed_reverse(apps, schema_editor):
    RefreshStatus = apps.get_model("phone_refresh", "RefreshStatus")
    CustomerMessage = apps.get_model("phone_refresh", "CustomerMessage")
    RefreshLog = apps.get_model("phone_refresh", "RefreshLog")
    for code, *_ in NEW_STATUSES:
        status = RefreshStatus.objects.filter(code=code).first()
        if status is None or RefreshLog.objects.filter(status=status).exists():
            continue
        CustomerMessage.objects.filter(status=status).delete()
        status.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("phone_refresh", "0018_alter_refreshlog_source"),
    ]

    operations = [
        migrations.AddField(
            model_name="systemsettings",
            name="refresh_wait_seconds",
            field=models.PositiveIntegerField(
                default=90,
                help_text="Longest a customer waits for a refresh before being told it is still processing (in seconds).",
            ),
        ),
        migrations.RunPython(seed_forward, seed_reverse),
    ]
