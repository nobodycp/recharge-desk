"""Add a second Sky ``not_found`` rule for the alternative 500 HTML.

Sky's upstream sometimes returns a generic "Refresh Sims" 500 error
page (instead of the normal Sky Telecom HTML) when the queried number
doesn't exist in the system. The page is in Arabic/RTL and contains:

    <title>Refresh Sims</title>
    ...
    حدث خطأ أثناء معالجة طلبك

The existing not_found rule keys off the Sky-Telecom phrase
``الرقم غير موجود بالنظام``, which never appears on this fallback
page, so those responses fall through to ``error``. This migration
seeds an additional ``contains`` rule on the same ``not_found``
status that matches ``حدث خطأ أثناء معالجة طلبك`` at order ``15``
(between the existing not_found at 10 and refreshed at 20), keeping
the original Sky-Telecom wording as the higher-priority match.
"""
from django.db import migrations


PATTERN = "حدث خطأ أثناء معالجة طلبك"
NOTE = "Sky alt 500 page (Refresh Sims): number not found"


def forward(apps, schema_editor):
    ProviderResponseRule = apps.get_model("phone_refresh", "ProviderResponseRule")
    RefreshStatus = apps.get_model("phone_refresh", "RefreshStatus")

    not_found_status = RefreshStatus.objects.get(code="not_found")

    ProviderResponseRule.objects.update_or_create(
        provider="sky",
        pattern=PATTERN,
        target_status=not_found_status,
        defaults={
            "match_type": "contains",
            "expected_value": "",
            "order": 15,
            "is_active": True,
            "note": NOTE,
        },
    )


def reverse_code(apps, schema_editor):
    ProviderResponseRule = apps.get_model("phone_refresh", "ProviderResponseRule")
    RefreshStatus = apps.get_model("phone_refresh", "RefreshStatus")

    not_found_status = RefreshStatus.objects.filter(code="not_found").first()
    if not_found_status is None:
        return

    ProviderResponseRule.objects.filter(
        provider="sky",
        pattern=PATTERN,
        target_status=not_found_status,
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("phone_refresh", "0008_fix_sky_wait_pattern"),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code),
    ]
