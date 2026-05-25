"""Seed Flask-parity social URLs on the ``SiteSettings`` singleton.

The original ``refresh_numbers`` app always rendered WhatsApp/Facebook
icons with hardcoded defaults. Migration ``0013`` added the fields as
empty strings, so the public page hid the block until an admin filled
them in. This data migration back-fills only rows that still have blank
URLs so production picks up the icons after ``migrate``.
"""
from django.db import migrations, models

DEFAULT_WHATSAPP_URL = "https://wa.me/972555544071"
DEFAULT_FACEBOOK_URL = "https://www.facebook.com/profile.php?id=61561099095296"


def seed_social_defaults(apps, schema_editor):
    SiteSettings = apps.get_model("phone_refresh", "SiteSettings")
    try:
        obj = SiteSettings.objects.get(pk=1)
    except SiteSettings.DoesNotExist:
        SiteSettings.objects.create(
            pk=1,
            public_subdomain="",
            redirect_main_to_subdomain=False,
            whatsapp_url=DEFAULT_WHATSAPP_URL,
            facebook_url=DEFAULT_FACEBOOK_URL,
        )
        return

    updates = {}
    if not (obj.whatsapp_url or "").strip():
        updates["whatsapp_url"] = DEFAULT_WHATSAPP_URL
    if not (obj.facebook_url or "").strip():
        updates["facebook_url"] = DEFAULT_FACEBOOK_URL
    if updates:
        SiteSettings.objects.filter(pk=1).update(**updates)


class Migration(migrations.Migration):

    dependencies = [
        ("phone_refresh", "0013_sitesettings_socials"),
    ]

    operations = [
        migrations.AlterField(
            model_name="sitesettings",
            name="whatsapp_url",
            field=models.CharField(
                blank=True,
                default=DEFAULT_WHATSAPP_URL,
                help_text=(
                    "رابط WhatsApp كامل (مثال: https://wa.me/970599999999). "
                    "اتركه فارغاً لإخفاء الأيقونة."
                ),
                max_length=255,
            ),
        ),
        migrations.AlterField(
            model_name="sitesettings",
            name="facebook_url",
            field=models.CharField(
                blank=True,
                default=DEFAULT_FACEBOOK_URL,
                help_text="رابط صفحة Facebook كامل. اتركه فارغاً لإخفاء الأيقونة.",
                max_length=255,
            ),
        ),
        migrations.RunPython(seed_social_defaults, migrations.RunPython.noop),
    ]
