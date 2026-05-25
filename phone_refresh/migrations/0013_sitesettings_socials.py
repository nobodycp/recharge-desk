"""Add WhatsApp + Facebook URL fields to ``SiteSettings``.

Both default to an empty string so existing rows keep working and the
public refresh page hides the social block until an admin fills in at
least one URL from the "إدارة الموقع" tab.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("phone_refresh", "0012_sitesettings"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesettings",
            name="whatsapp_url",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "رابط WhatsApp كامل (مثال: https://wa.me/970599999999). "
                    "اتركه فارغاً لإخفاء الأيقونة."
                ),
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="sitesettings",
            name="facebook_url",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "رابط صفحة Facebook كامل. اتركه فارغاً لإخفاء الأيقونة."
                ),
                max_length=255,
            ),
        ),
    ]
