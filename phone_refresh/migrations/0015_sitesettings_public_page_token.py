"""Add public-page token fields to ``SiteSettings`` and tighten API defaults."""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("phone_refresh", "0014_seed_social_defaults"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesettings",
            name="public_page_token",
            field=models.ForeignKey(
                blank=True,
                help_text="التوكن المستخدم لمصادقة طلبات صفحة التحديث العامة.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="phone_refresh.apitoken",
            ),
        ),
        migrations.AddField(
            model_name="sitesettings",
            name="public_page_token_raw",
            field=models.CharField(
                blank=True,
                default="",
                help_text="القيمة الخام للتوكن (تُحقَن في الصفحة العامة عند التحميل).",
                max_length=128,
            ),
        ),
        migrations.AlterField(
            model_name="apisettings",
            name="allow_anonymous_test_page",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "When ON, the public /phone-refresh/ form remains accessible "
                    "without a token even when require_token is ON."
                ),
            ),
        ),
    ]
