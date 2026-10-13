# Generated manually — recover numbers hidden after PR #43.

from django.db import migrations
from django.utils import timezone


def clear_legacy_dismissals(apps, schema_editor):
    """Old ``is_dismissed`` meant “hide from disconnect list”, not inventory.

    PR #43 started excluding dismissed keys from بيان الأرقام, so numbers
    dismissed under the old meaning vanished from the statement even though
    they still have sales. Clear the flag once so inventory is restored;
    future Deletes still set ``is_dismissed`` intentionally.
    """
    PhoneLineTracking = apps.get_model("reports", "PhoneLineTracking")
    PhoneLineTracking.objects.filter(is_dismissed=True).update(
        is_dismissed=False,
        updated_at=timezone.now(),
    )


def noop_reverse(apps, schema_editor):
    # Cannot restore which rows were legacy vs intentional deletes.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0003_phone_line_notes_and_dismiss_help"),
    ]

    operations = [
        migrations.RunPython(clear_legacy_dismissals, noop_reverse),
    ]
