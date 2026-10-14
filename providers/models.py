from __future__ import annotations

from django.db import models

from providers.registry import ADAPTERS

PROVIDER_KEY_CHOICES = [(key, key) for key in ADAPTERS]


class ProviderAccount(models.Model):
    """Login credentials for a provider, stored encrypted (write-only in the UI)."""

    provider_key = models.CharField(
        max_length=32, unique=True, choices=PROVIDER_KEY_CHOICES
    )
    is_enabled = models.BooleanField(default=True)
    username = models.CharField(max_length=150, blank=True)
    secret_encrypted = models.TextField(blank=True)
    totp_secret_encrypted = models.TextField(blank=True)
    # Encrypted JSON object, e.g. {"proxy_url": "..."}.
    extra_encrypted = models.TextField(blank=True)
    last_login_at = models.DateTimeField(null=True, blank=True)
    last_login_ok = models.BooleanField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.provider_key
