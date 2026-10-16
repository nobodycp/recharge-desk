from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_phone_manual_reset_and_threshold_85"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="can_provider_charge",
            field=models.BooleanField(
                default=False,
                help_text="يظهر زر «حفظ وشحن» لهذا الموظف. الإدارة تشحن بدون هذا الخيار.",
                verbose_name="السماح بشحن الخطوط عبر المزوّد",
            ),
        ),
    ]
