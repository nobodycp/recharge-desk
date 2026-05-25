"""Convert ``RefreshStatus`` from a ``TextChoices`` enum into a proper model.

Strategy (multi-step, data-preserving):

1. ``CreateModel`` the new ``RefreshStatus`` table.
2. ``RunPython`` seeds the five system rows (``refreshed``, ``not_found``,
   ``wait``, ``error``, ``service_off``) with ``is_system=True``.
3. ``AddField`` nullable FK columns alongside the existing string
   columns (``status_fk`` / ``target_status_fk``) on
   ``CustomerMessage`` / ``ProviderResponseRule`` / ``RefreshLog``.
4. ``RunPython`` backfills every FK from the corresponding string code.
5. ``RemoveField`` the legacy string columns.
6. ``RenameField`` the new FK columns back to ``status`` /
   ``target_status``.
7. ``AlterField`` to drop ``null=True`` (data is now guaranteed
   non-null) and pin ``on_delete=PROTECT``; the ``CustomerMessage``
   column is upgraded from ``ForeignKey`` to ``OneToOneField`` to keep
   the one-message-per-status invariant.

The reverse migration intentionally raises — re-introducing a
``TextChoices`` enum from arbitrary admin-edited status rows would
silently lose any custom codes. Roll back to ``0004`` only on a fresh
DB or after restoring from a backup.
"""
from django.db import migrations, models


SYSTEM_STATUSES = [
    ("refreshed", "تم التحديث", 10),
    ("not_found", "غير موجود", 20),
    ("wait", "انتظار", 30),
    ("error", "خطأ", 40),
    ("service_off", "الخدمة متوقفة", 50),
]


def seed_system_statuses(apps, schema_editor):
    RefreshStatus = apps.get_model("phone_refresh", "RefreshStatus")
    for code, label, sort_order in SYSTEM_STATUSES:
        RefreshStatus.objects.update_or_create(
            code=code,
            defaults={
                "label": label,
                "is_system": True,
                "sort_order": sort_order,
            },
        )


def unseed_system_statuses(apps, schema_editor):
    RefreshStatus = apps.get_model("phone_refresh", "RefreshStatus")
    RefreshStatus.objects.filter(
        code__in=[code for code, _, _ in SYSTEM_STATUSES]
    ).delete()


def backfill_status_fks(apps, schema_editor):
    """Translate every legacy string column into the new FK column.

    Rows whose legacy string doesn't match any seeded ``RefreshStatus``
    are remapped to ``error`` so the subsequent ``RemoveField`` +
    non-null ``AlterField`` succeed without an integrity error. This
    should be a no-op in practice — the only legacy values are the five
    canonical codes we just seeded plus the (already-migrated)
    ``cooldown`` value, which is handled below.
    """
    RefreshStatus = apps.get_model("phone_refresh", "RefreshStatus")
    CustomerMessage = apps.get_model("phone_refresh", "CustomerMessage")
    ProviderResponseRule = apps.get_model("phone_refresh", "ProviderResponseRule")
    RefreshLog = apps.get_model("phone_refresh", "RefreshLog")

    code_to_id = {row.code: row.pk for row in RefreshStatus.objects.all()}
    fallback_id = code_to_id["error"]

    def resolve(code: str) -> int:
        if not code:
            return fallback_id
        # ``cooldown`` was renamed to ``wait`` in migration 0004, but
        # historical rows could still slip through if migrations were
        # interrupted; guard for it.
        if code == "cooldown":
            return code_to_id["wait"]
        return code_to_id.get(code, fallback_id)

    for msg in CustomerMessage.objects.all():
        msg.status_fk_id = resolve(msg.status)
        msg.save(update_fields=["status_fk"])

    for rule in ProviderResponseRule.objects.all():
        rule.target_status_fk_id = resolve(rule.target_status)
        rule.save(update_fields=["target_status_fk"])

    # ``RefreshLog`` can be huge — bulk-update in a single SQL pass per code.
    for code, pk in code_to_id.items():
        RefreshLog.objects.filter(status=code).update(status_fk_id=pk)
    # Sweep any stragglers (orphaned/cooldown rows) into the error bucket.
    RefreshLog.objects.filter(status_fk__isnull=True).update(status_fk_id=fallback_id)


def reverse_not_supported(apps, schema_editor):
    raise NotImplementedError(
        "Reversing 0005_refresh_status_model would lose any custom statuses. "
        "Restore from backup if you need to roll back."
    )


class Migration(migrations.Migration):

    dependencies = [
        ("phone_refresh", "0004_merge_cooldown_into_wait"),
    ]

    operations = [
        # 1. New table.
        migrations.CreateModel(
            name="RefreshStatus",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.SlugField(max_length=40, unique=True, help_text="English slug used in API responses, e.g. 'refreshed', 'queued'. Lowercase letters, digits, dashes and underscores only.")),
                ("label", models.CharField(max_length=100, help_text="Arabic display name, e.g. 'تم التحديث'.")),
                ("is_system", models.BooleanField(default=False, help_text="System-defined statuses cannot be deleted from the UI.")),
                ("sort_order", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Refresh status",
                "verbose_name_plural": "Refresh statuses",
                "ordering": ["sort_order", "id"],
            },
        ),
        # 2. Seed system rows.
        migrations.RunPython(seed_system_statuses, unseed_system_statuses),
        # 3. Add nullable FK columns alongside the legacy string columns.
        migrations.AddField(
            model_name="customermessage",
            name="status_fk",
            field=models.ForeignKey(
                null=True,
                on_delete=models.deletion.PROTECT,
                related_name="+",
                to="phone_refresh.refreshstatus",
            ),
        ),
        migrations.AddField(
            model_name="providerresponserule",
            name="target_status_fk",
            field=models.ForeignKey(
                null=True,
                on_delete=models.deletion.PROTECT,
                related_name="+",
                to="phone_refresh.refreshstatus",
            ),
        ),
        migrations.AddField(
            model_name="refreshlog",
            name="status_fk",
            field=models.ForeignKey(
                null=True,
                on_delete=models.deletion.PROTECT,
                related_name="+",
                to="phone_refresh.refreshstatus",
            ),
        ),
        # 4. Translate legacy strings → FK ids.
        migrations.RunPython(backfill_status_fks, reverse_not_supported),
        # 5. Drop the legacy string columns. (Indexes that referenced them
        # are dropped with the column.)
        migrations.RemoveIndex(
            model_name="refreshlog",
            name="phone_refre_status_3b3837_idx",
        ),
        migrations.RemoveField(model_name="customermessage", name="status"),
        migrations.RemoveField(model_name="providerresponserule", name="target_status"),
        migrations.RemoveField(model_name="refreshlog", name="status"),
        # 6. Rename the FK columns to the canonical names.
        migrations.RenameField(
            model_name="customermessage", old_name="status_fk", new_name="status",
        ),
        migrations.RenameField(
            model_name="providerresponserule",
            old_name="target_status_fk",
            new_name="target_status",
        ),
        migrations.RenameField(
            model_name="refreshlog", old_name="status_fk", new_name="status",
        ),
        # 7. Lock down: non-nullable, PROTECT, related_name, and OneToOne
        # for the message table.
        migrations.AlterField(
            model_name="customermessage",
            name="status",
            field=models.OneToOneField(
                on_delete=models.deletion.PROTECT,
                related_name="message",
                to="phone_refresh.refreshstatus",
            ),
        ),
        migrations.AlterField(
            model_name="providerresponserule",
            name="target_status",
            field=models.ForeignKey(
                on_delete=models.deletion.PROTECT,
                related_name="rules",
                to="phone_refresh.refreshstatus",
            ),
        ),
        migrations.AlterField(
            model_name="refreshlog",
            name="status",
            field=models.ForeignKey(
                on_delete=models.deletion.PROTECT,
                related_name="log_entries",
                to="phone_refresh.refreshstatus",
            ),
        ),
        migrations.AlterModelOptions(
            name="customermessage",
            options={"ordering": ["status__sort_order", "status__id"]},
        ),
        # Re-create the (status, -created_at) index now that ``status`` is
        # an FK column on the same table.
        migrations.AddIndex(
            model_name="refreshlog",
            index=models.Index(
                fields=["status", "-created_at"],
                name="phone_refre_status__c2a732_idx",
            ),
        ),
    ]
