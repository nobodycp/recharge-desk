from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("providers", "0006_providerjob_number_info"),
    ]

    operations = [
        migrations.AddField(
            model_name="providerconnection",
            name="number_info_enabled",
            field=models.BooleanField(default=True),
        ),
    ]
