"""Add ``SiteSettings`` singleton for the public-host (subdomain) routing config.

The new model carries two knobs an admin can toggle from the
"إدارة الموقع" tab:

* ``public_subdomain`` — when set, ``PhoneRefreshSubdomainMiddleware``
  serves ONLY the public refresh page on this host (every other path
  returns 404).
* ``redirect_main_to_subdomain`` — when on, any hit to
  ``/phone-refresh/`` on the main host is 302-redirected to the
  configured subdomain.

The migration seeds the singleton at ``pk=1`` so callers can always
rely on ``SiteSettings.get_solo()`` without an extra create branch.
"""
from django.db import migrations, models


def seed_solo(apps, schema_editor):
    SiteSettings = apps.get_model("phone_refresh", "SiteSettings")
    SiteSettings.objects.get_or_create(
        pk=1,
        defaults={
            "public_subdomain": "",
            "redirect_main_to_subdomain": False,
        },
    )


def seed_reverse(apps, schema_editor):
    SiteSettings = apps.get_model("phone_refresh", "SiteSettings")
    SiteSettings.objects.filter(pk=1).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("phone_refresh", "0011_refreshlog_source"),
    ]

    operations = [
        migrations.CreateModel(
            name="SiteSettings",
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
                    "public_subdomain",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text=(
                            "مثال: rn.prosim.ps — اتركه فارغاً "
                            "لتعطيل التوجيه عبر سب دومين."
                        ),
                        max_length=255,
                    ),
                ),
                (
                    "redirect_main_to_subdomain",
                    models.BooleanField(
                        default=False,
                        help_text=(
                            "عند التفعيل: زيارة /phone-refresh/ على الدومين "
                            "الرئيسي يحوّل تلقائياً إلى السب دومين."
                        ),
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "إعدادات الموقع",
                "verbose_name_plural": "إعدادات الموقع",
            },
        ),
        migrations.RunPython(seed_solo, seed_reverse),
    ]
