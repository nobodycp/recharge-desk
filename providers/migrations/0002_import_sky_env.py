"""Import Sky credentials from SKY_SALES_* env vars into an encrypted account."""
import json

from django.db import migrations


def import_sky_env(apps, schema_editor):
    from providers.accounts import credentials_from_env
    from providers.crypto import encrypt

    ProviderAccount = apps.get_model("providers", "ProviderAccount")
    if ProviderAccount.objects.filter(provider_key="sky").exists():
        return
    creds = credentials_from_env()
    if creds is None:
        return
    ProviderAccount.objects.create(
        provider_key="sky",
        is_enabled=True,
        username=creds.username,
        secret_encrypted=encrypt(creds.password),
        totp_secret_encrypted=encrypt(creds.totp_secret) if creds.totp_secret else "",
        extra_encrypted=encrypt(json.dumps(creds.extra)) if creds.extra else "",
    )


class Migration(migrations.Migration):

    dependencies = [
        ("providers", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(import_sky_env, migrations.RunPython.noop),
    ]
