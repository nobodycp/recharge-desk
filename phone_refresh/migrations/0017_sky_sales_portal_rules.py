"""Sky Sales Portal — match rules for sales-ps.sky5g.ps JSON responses."""
from django.db import migrations

NEW_SKY_RULES = [
    ("json_path", "$.reason", "success", "refreshed", 10, "Sky Sales: refreshed"),
    ("json_path", "$.reason", "not_found", "not_found", 20, "Sky Sales: not found"),
    ("json_path", "$.reason", "not_eligible", "wait", 30, "Sky Sales: cooldown"),
    ("json_path", "$.reason", "not_active", "error", 40, "Sky Sales: not active"),
]


def forward(apps, schema_editor):
    ProviderResponseRule = apps.get_model("phone_refresh", "ProviderResponseRule")
    RefreshStatus = apps.get_model("phone_refresh", "RefreshStatus")

    ProviderResponseRule.objects.filter(provider="sky").update(is_active=False)

    for match_type, pattern, expected, target_code, order, note in NEW_SKY_RULES:
        target_status = RefreshStatus.objects.get(code=target_code)
        ProviderResponseRule.objects.update_or_create(
            provider="sky",
            match_type=match_type,
            pattern=pattern,
            expected_value=expected,
            defaults={
                "target_status": target_status,
                "order": order,
                "is_active": True,
                "note": note,
            },
        )


def reverse(apps, schema_editor):
    ProviderResponseRule = apps.get_model("phone_refresh", "ProviderResponseRule")

    for match_type, pattern, expected, *_rest in NEW_SKY_RULES:
        ProviderResponseRule.objects.filter(
            provider="sky",
            match_type=match_type,
            pattern=pattern,
            expected_value=expected,
        ).delete()

    ProviderResponseRule.objects.filter(provider="sky").update(is_active=True)


class Migration(migrations.Migration):
    dependencies = [
        ("phone_refresh", "0016_alter_refreshlog_source"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
