"""Add ``RefreshLog.raw_body`` to persist the full upstream response.

The existing ``raw_excerpt`` field caps at ~500 chars, which is enough
for the list view but useless for debugging per-number failures. The
new ``raw_body`` column stores the complete response body (truncated to
``MAX_RAW_BODY_CHARS`` in the service layer to avoid runaway growth) so
the reports detail modal can show admins exactly what the provider
returned.

No data backfill: pre-existing rows simply have an empty ``raw_body``
and the UI shows a "not stored for this entry" placeholder.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("phone_refresh", "0009_sky_not_found_alt_pattern"),
    ]

    operations = [
        migrations.AddField(
            model_name="refreshlog",
            name="raw_body",
            field=models.TextField(blank=True, default=""),
        ),
    ]
