from __future__ import annotations

import uuid

from django.db import models

from providers.contracts import Capability
from providers.registry import ADAPTERS

PROVIDER_KEY_CHOICES = [(key, key) for key in ADAPTERS]


class ProviderConnection(models.Model):
    """Provider login credentials (stored encrypted, write-only in the UI) and
    the per-service switches for that provider."""

    SERVICE_FIELDS = {
        Capability.REFRESH: "refresh_enabled",
        Capability.CHARGE: "charge_enabled",
        Capability.RECONCILE: "reconcile_enabled",
    }

    provider_key = models.CharField(
        max_length=32, unique=True, choices=PROVIDER_KEY_CHOICES
    )
    is_enabled = models.BooleanField(default=True)
    refresh_enabled = models.BooleanField(default=True)
    charge_enabled = models.BooleanField(default=False)
    reconcile_enabled = models.BooleanField(default=True)
    username = models.CharField(max_length=150, blank=True)
    secret_encrypted = models.TextField(blank=True)
    totp_secret_encrypted = models.TextField(blank=True)
    # Encrypted JSON object, e.g. {"proxy_url": "..."}.
    extra_encrypted = models.TextField(blank=True)
    last_login_at = models.DateTimeField(null=True, blank=True)
    last_login_ok = models.BooleanField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "providers_provideraccount"

    @classmethod
    def for_key(cls, provider_key: str) -> ProviderConnection:
        connection, _ = cls.objects.get_or_create(provider_key=provider_key)
        return connection

    def __str__(self) -> str:
        return self.provider_key


class ProviderJob(models.Model):
    """Durable record of one asynchronous provider operation (refresh, charge, ...)."""

    KIND_REFRESH = "refresh"
    KIND_CHARGE = "charge"
    KIND_CHECK_CHARGE = "check_charge"
    KIND_CHOICES = [
        (KIND_REFRESH, "refresh"),
        (KIND_CHARGE, "charge"),
        (KIND_CHECK_CHARGE, "check_charge"),
    ]

    STATUS_QUEUED = "queued"
    STATUS_RUNNING = "running"
    STATUS_DONE = "done"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_QUEUED, "queued"),
        (STATUS_RUNNING, "running"),
        (STATUS_DONE, "done"),
        (STATUS_FAILED, "failed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    provider_key = models.CharField(max_length=32)
    phone = models.CharField(max_length=20, db_index=True)
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default=STATUS_QUEUED
    )
    source = models.CharField(max_length=20, blank=True)
    idempotency_key = models.CharField(
        max_length=100, unique=True, null=True, blank=True
    )
    payload = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["kind", "phone", "status", "created_at"],
                name="providerjob_inflight_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind}:{self.provider_key}:{self.status}"
