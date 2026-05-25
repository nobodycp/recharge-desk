"""Fix the Sky ``wait`` rule pattern.

The original seed (``0002_seed_defaults``) used the phrase

    لقد قمت بتحديث الرقم قبل قليل الرجاء الانتظار قليلا

but the live Sky upstream actually emits the shorter

    لقد قمت بتحديث الرقم قبل قليل الرجاء الانتظار

(without the trailing ``قليلا``) embedded inside its
``<div class="notifications">`` HTML block. The longer seeded pattern
therefore never matches and the response falls through to ``error``.
This migration trims the trailing word from the existing rule so the
matcher correctly classifies the response as ``wait``.

The fix is scoped narrowly to ``(provider="sky",
target_status__code="wait")`` rows whose pattern equals the legacy
phrase, to avoid accidentally rewriting any admin-customised rule.
"""
from django.db import migrations


OLD_PATTERN = "لقد قمت بتحديث الرقم قبل قليل الرجاء الانتظار قليلا"
NEW_PATTERN = "لقد قمت بتحديث الرقم قبل قليل الرجاء الانتظار"


def forward(apps, schema_editor):
    ProviderResponseRule = apps.get_model("phone_refresh", "ProviderResponseRule")
    ProviderResponseRule.objects.filter(
        provider="sky",
        target_status__code="wait",
        pattern=OLD_PATTERN,
    ).update(pattern=NEW_PATTERN)


def reverse(apps, schema_editor):
    ProviderResponseRule = apps.get_model("phone_refresh", "ProviderResponseRule")
    ProviderResponseRule.objects.filter(
        provider="sky",
        target_status__code="wait",
        pattern=NEW_PATTERN,
    ).update(pattern=OLD_PATTERN)


class Migration(migrations.Migration):
    dependencies = [
        ("phone_refresh", "0007_provider_config_and_status"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
