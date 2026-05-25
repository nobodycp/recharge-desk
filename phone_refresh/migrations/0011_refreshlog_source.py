"""Add ``RefreshLog.source`` so each refresh attempt is tagged by entry-point.

The new column distinguishes attempts triggered through:

* ``api``           — the public JSON endpoint (external clients).
* ``web``           — the public HTML form.
* ``internal_test`` — the admin Providers / General test panels.
* ``legacy``        — rows that existed before this migration.

We do the field add + default switch in a single migration:

1. :class:`AddField` with ``default="legacy"`` so every pre-existing row
   gets tagged ``legacy`` (the column is non-null and the default is
   applied during the schema change).
2. :class:`AlterField` switches the column default to ``"web"`` so that
   any future caller that forgets to pass ``source`` falls back to a
   real bucket (never ``legacy``).
3. A defensive :class:`RunPython` pass tags any row that somehow still
   has an empty / unknown ``source`` value as ``legacy`` (covers edge
   cases where the AddField default was not applied, e.g. partial DB
   states from a prior aborted migration).
"""
from django.db import migrations, models


def _backfill_legacy(apps, schema_editor):
    RefreshLog = apps.get_model("phone_refresh", "RefreshLog")
    valid = {"api", "web", "internal_test", "legacy"}
    RefreshLog.objects.exclude(source__in=valid).update(source="legacy")
    RefreshLog.objects.filter(source="").update(source="legacy")


def _noop(apps, schema_editor):
    return


class Migration(migrations.Migration):

    dependencies = [
        ("phone_refresh", "0010_refreshlog_raw_body"),
    ]

    operations = [
        migrations.AddField(
            model_name="refreshlog",
            name="source",
            field=models.CharField(
                choices=[
                    ("api", "API"),
                    ("web", "Web"),
                    ("internal_test", "Internal Test"),
                    ("legacy", "Legacy"),
                ],
                db_index=True,
                default="legacy",
                help_text="Where the refresh attempt was triggered from.",
                max_length=20,
            ),
        ),
        migrations.RunPython(_backfill_legacy, _noop),
        migrations.AlterField(
            model_name="refreshlog",
            name="source",
            field=models.CharField(
                choices=[
                    ("api", "API"),
                    ("web", "Web"),
                    ("internal_test", "Internal Test"),
                    ("legacy", "Legacy"),
                ],
                db_index=True,
                default="web",
                help_text="Where the refresh attempt was triggered from.",
                max_length=20,
            ),
        ),
    ]
