from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("providers", "0007_number_info_enabled"),
    ]

    operations = [
        migrations.AddField(
            model_name="providerconnection",
            name="session_encrypted",
            field=models.TextField(blank=True),
        ),
    ]
