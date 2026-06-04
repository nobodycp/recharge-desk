"""Data models for the SMS-based number refresh gateway.

The gateway receives subscriber SMS via an Android device, extracts a
Palestinian mobile number, calls a configurable refresh API (by default
the existing phone-refresh endpoint), and replies to the sender through
an outbox that one or more devices poll. Everything is admin-controlled
from the "تحديث الرسائل" panel.
"""
from __future__ import annotations

from django.conf import settings
from django.core.cache import cache
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

SMS_SETTINGS_CACHE_KEY = "sms_gateway:settings:singleton"
SMS_SETTINGS_CACHE_TTL = 300


class SmsGatewaySettings(models.Model):
    """Singleton (pk=1) holding all gateway toggles and the refresh API gateway."""

    service_enabled = models.BooleanField(
        _("service enabled"),
        default=True,
        help_text=_("When off, no real refresh runs; optionally reply with the service-off message."),
    )
    send_service_off_reply = models.BooleanField(
        _("reply when service is off"),
        default=False,
    )
    service_off_message = models.TextField(_("service-off message"), blank=True)

    replies_master_enabled = models.BooleanField(
        _("master replies switch"),
        default=True,
        help_text=_("Global kill-switch for ALL outgoing replies (saves SMS consumption)."),
    )

    test_number = models.CharField(_("test number"), max_length=20, blank=True)
    test_reply_message = models.TextField(
        _("test reply message"),
        blank=True,
        default="الخدمة تعمل بشكل سليم.",
    )

    claim_timeout_seconds = models.PositiveIntegerField(
        _("claim timeout (seconds)"),
        default=120,
        help_text=_("Re-queue a reply if a device claimed it but did not confirm sending in time."),
    )
    auto_pause_threshold = models.PositiveIntegerField(
        _("auto-pause after consecutive failures"),
        default=5,
        help_text=_("0 disables auto-pausing devices on repeated send failures."),
    )
    sender_max_messages = models.PositiveIntegerField(
        _("max messages per sender"),
        default=0,
        help_text=_("0 = no per-sender limit."),
    )
    sender_window_minutes = models.PositiveIntegerField(
        _("sender window (minutes)"),
        default=60,
    )
    global_daily_cap = models.PositiveIntegerField(
        _("global daily reply cap"),
        default=0,
        help_text=_("0 = unlimited total replies per day across all devices."),
    )
    allowlist_mode = models.BooleanField(
        _("allowlist mode"),
        default=False,
        help_text=_("When on, only numbers in the allow list are processed."),
    )
    max_send_attempts = models.PositiveIntegerField(
        _("max send attempts"),
        default=3,
    )
    log_retention_days = models.PositiveIntegerField(
        _("log retention (days)"),
        default=0,
        help_text=_("0 = keep forever. Older inbound/outbound rows are purged by sms_purge_logs."),
    )
    heartbeat_alert_minutes = models.PositiveIntegerField(
        _("heartbeat alert (minutes)"),
        default=10,
        help_text=_("Warn when an active device has not contacted the server within this window."),
    )

    # Configurable refresh API gateway (defaults to the internal endpoint).
    refresh_api_url = models.CharField(
        _("refresh API URL"),
        max_length=500,
        blank=True,
        help_text=_("Full URL of the refresh API. Leave empty to use the internal phone-refresh endpoint."),
    )
    refresh_api_token = models.CharField(_("refresh API token"), max_length=255, blank=True)
    refresh_api_timeout = models.PositiveIntegerField(_("refresh API timeout (s)"), default=30)
    refresh_api_phone_field = models.CharField(
        _("phone field name"),
        max_length=60,
        default="phone_number",
    )
    refresh_api_status_path = models.CharField(_("status path"), max_length=120, default="status")
    refresh_api_title_path = models.CharField(_("title path"), max_length=120, default="message.title")
    refresh_api_body_path = models.CharField(_("body path"), max_length=120, default="message.body")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "SMS gateway settings"
        verbose_name_plural = "SMS gateway settings"

    def __str__(self) -> str:
        return "SMS gateway settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
        cache.delete(SMS_SETTINGS_CACHE_KEY)

    def delete(self, *args, **kwargs):  # pragma: no cover — guard rail
        return

    @classmethod
    def load(cls) -> "SmsGatewaySettings":
        if SMS_SETTINGS_CACHE_TTL:
            cached = cache.get(SMS_SETTINGS_CACHE_KEY)
            if cached is not None:
                return cached
        instance, _created = cls.objects.get_or_create(pk=1)
        if SMS_SETTINGS_CACHE_TTL:
            cache.set(SMS_SETTINGS_CACHE_KEY, instance, timeout=SMS_SETTINGS_CACHE_TTL)
        return instance


class SmsGatewayDevice(models.Model):
    """A registered Android gateway phone (main or backup)."""

    name = models.CharField(_("name"), max_length=120)
    phone_number = models.CharField(_("phone number"), max_length=20, blank=True)
    token_hash = models.CharField(max_length=64, unique=True)
    token_prefix = models.CharField(max_length=10, blank=True)
    priority = models.PositiveIntegerField(
        _("priority"),
        default=100,
        help_text=_("Lower = preferred for sending replies."),
    )
    is_active = models.BooleanField(_("active"), default=True)
    can_send = models.BooleanField(
        _("can send replies"),
        default=True,
        help_text=_("Off = receives only; replies go out from other devices."),
    )
    consecutive_failures = models.PositiveIntegerField(default=0)
    auto_paused_at = models.DateTimeField(null=True, blank=True)
    daily_send_limit = models.PositiveIntegerField(
        _("daily send limit"),
        default=0,
        help_text=_("0 = unlimited for this device."),
    )
    sent_today = models.PositiveIntegerField(default=0)
    sent_today_date = models.DateField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sms_gateway_devices",
    )
    notes = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["priority", "id"]
        verbose_name = "SMS gateway device"
        verbose_name_plural = "SMS gateway devices"

    def __str__(self) -> str:
        return f"{self.name} ({self.phone_number or '—'})"

    @property
    def is_auto_paused(self) -> bool:
        return self.auto_paused_at is not None

    @property
    def is_sendable(self) -> bool:
        return self.is_active and self.can_send and not self.is_auto_paused

    def roll_daily_counter(self) -> None:
        today = timezone.localdate()
        if self.sent_today_date != today:
            self.sent_today_date = today
            self.sent_today = 0


class SmsReplyPolicy(models.Model):
    """Per refresh-status reply toggle + optional SMS-specific override text."""

    status = models.OneToOneField(
        "phone_refresh.RefreshStatus",
        on_delete=models.CASCADE,
        related_name="sms_reply_policy",
    )
    reply_enabled = models.BooleanField(_("reply enabled"), default=True)
    message_override = models.TextField(_("SMS message override"), blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["status__sort_order", "status__id"]
        verbose_name = "SMS reply policy"
        verbose_name_plural = "SMS reply policies"

    def __str__(self) -> str:
        return f"{self.status_id}: {'on' if self.reply_enabled else 'off'}"


class SmsAccessRule(models.Model):
    """Block / allow list entry. ``value`` is a full number or a prefix."""

    class Mode(models.TextChoices):
        BLOCK = "block", _("Block")
        ALLOW = "allow", _("Allow")

    value = models.CharField(_("number or prefix"), max_length=20)
    mode = models.CharField(max_length=10, choices=Mode.choices, default=Mode.BLOCK)
    is_active = models.BooleanField(default=True)
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["mode", "value"]
        verbose_name = "SMS access rule"
        verbose_name_plural = "SMS access rules"

    def __str__(self) -> str:
        return f"{self.mode}:{self.value}"


class InboundSms(models.Model):
    """A subscriber SMS received by a gateway device."""

    class State(models.TextChoices):
        RECEIVED = "received", _("Received")
        PROCESSED = "processed", _("Processed")
        IGNORED = "ignored", _("Ignored (no valid number)")
        TEST = "test", _("Test")
        BLOCKED = "blocked", _("Blocked")
        DUPLICATE = "duplicate", _("Duplicate")
        RATE_LIMITED = "rate_limited", _("Rate limited")
        ERROR = "error", _("Error")

    device = models.ForeignKey(
        SmsGatewayDevice,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="inbound_messages",
    )
    from_number = models.CharField(_("from"), max_length=20, db_index=True)
    raw_text = models.TextField(_("raw text"), blank=True)
    extracted_number = models.CharField(_("extracted number"), max_length=20, blank=True)
    device_msg_id = models.CharField(max_length=80, blank=True)
    state = models.CharField(max_length=20, choices=State.choices, default=State.RECEIVED, db_index=True)
    refresh_log = models.ForeignKey(
        "phone_refresh.RefreshLog",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sms_inbound",
    )
    reply_text = models.TextField(blank=True)
    delete_requested = models.BooleanField(default=False, db_index=True)
    delete_confirmed_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-received_at"]
        verbose_name = "Inbound SMS"
        verbose_name_plural = "Inbound SMS"
        constraints = [
            models.UniqueConstraint(
                fields=["device", "device_msg_id"],
                condition=models.Q(device_msg_id__gt=""),
                name="sms_inbound_unique_device_msg",
            )
        ]
        indexes = [models.Index(fields=["state", "-received_at"])]

    def __str__(self) -> str:
        return f"{self.from_number} → {self.extracted_number or '?'} [{self.state}]"


class OutboundSms(models.Model):
    """A reply queued to be sent to a subscriber by any sendable device."""

    class State(models.TextChoices):
        QUEUED = "queued", _("Queued")
        CLAIMED = "claimed", _("Claimed")
        SENT = "sent", _("Sent")
        FAILED = "failed", _("Failed")
        DEAD_LETTER = "dead_letter", _("Dead letter")

    to_number = models.CharField(_("to"), max_length=20, db_index=True)
    body = models.TextField(_("body"))
    related_inbound = models.ForeignKey(
        InboundSms,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="outbound_messages",
    )
    state = models.CharField(max_length=20, choices=State.choices, default=State.QUEUED, db_index=True)
    claimed_by = models.ForeignKey(
        SmsGatewayDevice,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="claimed_messages",
    )
    claimed_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=3)
    error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "Outbound SMS"
        verbose_name_plural = "Outbound SMS"
        indexes = [models.Index(fields=["state", "created_at"])]

    def __str__(self) -> str:
        return f"→ {self.to_number} [{self.state}]"
