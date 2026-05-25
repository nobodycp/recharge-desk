"""Seed default ProviderResponseRules + CustomerMessages.

Patterns mirror the hardcoded substrings/codes that lived in the original
Flask ``refresh_numbers/rn.py`` so the new matcher returns the exact
same status on day one.
"""
from django.db import migrations

# (match_type, pattern, expected_value, target_status, order, note)
LAYAN_RULES = [
    ("contains", "لم يتم العثور على رقمك", "", "not_found", 10, "Layan: number not found"),
    ("contains", "يمكنك تحديث الرقم مرة كل خمس ساعات", "", "wait", 20, "Layan: 5h cooldown"),
    ("contains", "تم ارسال طلبك بنجاح", "", "refreshed", 30, "Layan: refresh accepted"),
]

ALOHA_RULES = [
    ("json_path_contains", "$.message", "Wrong Numbe", "not_found", 10, "Aloha: wrong number"),
    ("json_path_contains", "$.message", "number updated last 6 hours", "wait", 20, "Aloha: 6h cooldown"),
    ("json_path_contains", "$.message", "Phone number refreshed", "refreshed", 30, "Aloha: refresh accepted"),
    ("json_path_contains", "$.message", "number waiting in queue", "wait", 40, "Aloha: queued"),
]

AREEN_RULES = [
    ("json_path", "$.StatusCode", "993", "wait", 10, "Areen: 993 cooldown"),
    ("json_path", "$.StatusCode", "250", "not_found", 20, "Areen: 250 not found"),
    ("json_path", "$.StatusCode", "23", "refreshed", 30, "Areen: 23 refreshed"),
]

SKY_RULES = [
    ("contains", "الرقم غير موجود بالنظام", "", "not_found", 10, "Sky: not in system"),
    ("contains", "تم تحديث الرقم أعد تشغيل الجهاز خلال 10 دقاي", "", "refreshed", 20, "Sky: refreshed"),
    ("contains", "لقد قمت بتحديث الرقم قبل قليل الرجاء الانتظار قليلا", "", "wait", 30, "Sky: cooldown"),
]

SEED_RULES = {
    "layan": LAYAN_RULES,
    "aloha": ALOHA_RULES,
    "areen": AREEN_RULES,
    "sky": SKY_RULES,
}

CUSTOMER_MESSAGES = [
    ("refreshed", "تم التحديث", "تم التحديث بنجاح. الرجاء إغلاق الرقم لمدة عشر دقائق ثم تشغيله."),
    ("not_found", "رقم غير موجود", "لم يتم العثور على رقمك في النظام."),
    ("wait", "الرجاء الانتظار", "يرجى الانتظار قبل تحديث هذا الرقم مرة أخرى."),
    ("error", "خطأ", "حدث خطأ غير متوقع. الرجاء المحاولة لاحقاً."),
]


def seed_forward(apps, schema_editor):
    ProviderResponseRule = apps.get_model("phone_refresh", "ProviderResponseRule")
    CustomerMessage = apps.get_model("phone_refresh", "CustomerMessage")

    for provider, rules in SEED_RULES.items():
        for match_type, pattern, expected, target, order, note in rules:
            ProviderResponseRule.objects.get_or_create(
                provider=provider,
                order=order,
                match_type=match_type,
                pattern=pattern,
                defaults={
                    "expected_value": expected,
                    "target_status": target,
                    "is_active": True,
                    "note": note,
                },
            )

    for status, title, body in CUSTOMER_MESSAGES:
        CustomerMessage.objects.update_or_create(
            status=status,
            defaults={"title": title, "body": body},
        )


def seed_reverse(apps, schema_editor):
    ProviderResponseRule = apps.get_model("phone_refresh", "ProviderResponseRule")
    CustomerMessage = apps.get_model("phone_refresh", "CustomerMessage")
    seeded_providers = list(SEED_RULES.keys())
    ProviderResponseRule.objects.filter(provider__in=seeded_providers).delete()
    CustomerMessage.objects.filter(status__in=[s for s, *_ in CUSTOMER_MESSAGES]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("phone_refresh", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_forward, seed_reverse),
    ]
