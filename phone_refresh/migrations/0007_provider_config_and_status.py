"""Create ``ProviderConfig`` + seed 4 rows; add ``provider_off`` system status.

The ``provider_off`` row joins the seeded set of system statuses created
by 0005 and gets a default customer message so the public flow has
something to render when an admin disables a provider via the new
General tab on the Providers page.
"""
from django.db import migrations, models


PROVIDER_KEYS = ["sky", "aloha", "layan", "areen"]

PROVIDER_OFF_STATUS = {
    "code": "provider_off",
    "label": "المزوّد متوقف",
    "sort_order": 60,
}

PROVIDER_OFF_MESSAGE = {
    "title": "المزوّد غير متاح",
    "body": "هذا المزوّد متوقف مؤقتاً. الرجاء المحاولة لاحقاً.",
}


def seed_forward(apps, schema_editor):
    ProviderConfig = apps.get_model("phone_refresh", "ProviderConfig")
    RefreshStatus = apps.get_model("phone_refresh", "RefreshStatus")
    CustomerMessage = apps.get_model("phone_refresh", "CustomerMessage")

    for key in PROVIDER_KEYS:
        ProviderConfig.objects.get_or_create(
            provider=key,
            defaults={"is_enabled": True},
        )

    status, _ = RefreshStatus.objects.update_or_create(
        code=PROVIDER_OFF_STATUS["code"],
        defaults={
            "label": PROVIDER_OFF_STATUS["label"],
            "is_system": True,
            "sort_order": PROVIDER_OFF_STATUS["sort_order"],
        },
    )
    CustomerMessage.objects.update_or_create(
        status=status,
        defaults={
            "title": PROVIDER_OFF_MESSAGE["title"],
            "body": PROVIDER_OFF_MESSAGE["body"],
        },
    )


def seed_reverse(apps, schema_editor):
    ProviderConfig = apps.get_model("phone_refresh", "ProviderConfig")
    RefreshStatus = apps.get_model("phone_refresh", "RefreshStatus")
    CustomerMessage = apps.get_model("phone_refresh", "CustomerMessage")

    ProviderConfig.objects.filter(provider__in=PROVIDER_KEYS).delete()
    status = RefreshStatus.objects.filter(code=PROVIDER_OFF_STATUS["code"]).first()
    if status is not None:
        CustomerMessage.objects.filter(status=status).delete()
        status.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("phone_refresh", "0006_api_management"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProviderConfig",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "provider",
                    models.CharField(
                        choices=[
                            ("sky", "Sky"),
                            ("aloha", "Aloha"),
                            ("layan", "Layan"),
                            ("areen", "Areen"),
                        ],
                        db_index=True,
                        max_length=20,
                        unique=True,
                    ),
                ),
                ("is_enabled", models.BooleanField(default=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Provider config",
                "verbose_name_plural": "Provider configs",
                "ordering": ["provider"],
            },
        ),
        migrations.RunPython(seed_forward, seed_reverse),
    ]
