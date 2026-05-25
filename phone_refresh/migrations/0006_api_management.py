"""Create API management tables: ``ApiSettings`` (singleton) + ``ApiToken``.

Seeds an empty :class:`ApiSettings` singleton with default values so the
settings tab can render a bound form on first load. Tokens are created
through the management UI; nothing is seeded here.
"""
from django.conf import settings
from django.db import migrations, models


def seed_api_settings(apps, schema_editor):
    ApiSettings = apps.get_model("phone_refresh", "ApiSettings")
    # Insert defaults only on first apply — never overwrite admin edits if
    # this migration is re-run (e.g. django_migrations reset on deploy).
    ApiSettings.objects.get_or_create(
        pk=1,
        defaults={
            "require_token": False,
            "rate_limit_per_minute": 60,
            "rate_limit_per_hour": 600,
            "allow_anonymous_test_page": True,
            "allowed_origins": "",
        },
    )


def unseed_api_settings(apps, schema_editor):
    ApiSettings = apps.get_model("phone_refresh", "ApiSettings")
    ApiSettings.objects.filter(pk=1).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("phone_refresh", "0005_refresh_status_model"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ApiSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("require_token", models.BooleanField(default=False, help_text="When ON, the public API endpoint requires a valid Authorization: Bearer <token> header.")),
                ("rate_limit_per_minute", models.PositiveIntegerField(default=60, help_text="Max public API requests per IP per minute.")),
                ("rate_limit_per_hour", models.PositiveIntegerField(default=600, help_text="Max public API requests per IP per hour.")),
                ("allow_anonymous_test_page", models.BooleanField(default=True, help_text="When ON, the public /phone-refresh/ form remains accessible without a token even when require_token is ON.")),
                ("allowed_origins", models.TextField(blank=True, help_text="One origin per line. Empty = allow any origin.")),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "API settings",
                "verbose_name_plural": "API settings",
            },
        ),
        migrations.CreateModel(
            name="ApiToken",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(help_text="Friendly label for this token (e.g. where it's used).", max_length=120)),
                ("token_hash", models.CharField(help_text="sha256 of the raw token; raw value is shown only once on creation.", max_length=64, unique=True)),
                ("prefix", models.CharField(help_text="First 8 chars of the raw token, for identification in lists.", max_length=10)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("last_used_at", models.DateTimeField(blank=True, null=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.deletion.SET_NULL,
                        related_name="phone_refresh_api_tokens",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "API token",
                "verbose_name_plural": "API tokens",
                "ordering": ["-created_at"],
            },
        ),
        migrations.RunPython(seed_api_settings, unseed_api_settings),
    ]
