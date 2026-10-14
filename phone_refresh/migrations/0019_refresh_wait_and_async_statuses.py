"""Add ``refresh_wait_seconds`` and seed the ``processing`` / ``busy`` statuses.

Existing statuses and customer messages are never overwritten: an admin may
already have created or edited rows with these codes. The reverse is a no-op
for the same reason (and rows may be referenced by logs or rules).
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
        migrations.RunPython(seed_forward, migrations.RunPython.noop),
    ]
