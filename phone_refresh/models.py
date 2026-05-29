from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class PhoneProvider(models.TextChoices):
    SKY = "sky", "Sky"
    ALOHA = "aloha", "Aloha"
    LAYAN = "layan", "Layan"
    AREEN = "areen", "Areen"


class RefreshStatus(models.Model):
    """Admin-managed catalog of possible refresh outcomes.

    The ``code`` column is the English slug returned in the public API
    (``"refreshed"``, ``"wait"``, etc.); ``label`` is the Arabic display
    name shown in admin dropdowns, the messages tab, the rule editor and
    every customer-facing surface. Rows flagged ``is_system=True`` are
    seeded by the migrations and cannot be deleted from the UI (they're
    the contract the rest of the codebase relies on).

    Other models point at this table with ``on_delete=PROTECT`` so a
    status that's still referenced anywhere — customer message, provider
    rule, refresh log — cannot be deleted by mistake.
    """

    SYSTEM_CODES = {
        "refreshed",
        "not_found",
        "wait",
        "error",
        "service_off",
        "provider_off",
    }

    code = models.SlugField(
        max_length=40,
        unique=True,
        help_text=_(
            "English slug used in API responses, e.g. 'refreshed', 'queued'. "
            "Lowercase letters, digits, dashes and underscores only."
        ),
    )
    label = models.CharField(
        max_length=100,
        help_text=_("Arabic display name, e.g. 'تم التحديث'."),
    )
    is_system = models.BooleanField(
        default=False,
        help_text=_("System-defined statuses cannot be deleted from the UI."),
    )
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "Refresh status"
        verbose_name_plural = "Refresh statuses"

    # ---- Single-process cache --------------------------------------------------
    # ``get_by_code`` is hot — called once per refresh attempt for several
    # statuses. We memoize on the class so repeat lookups don't hit the
    # DB. ``save`` / ``delete`` clear the cache locally; in multi-process
    # deployments a stale label can survive until the next save in that
    # worker, which is acceptable since the only thing that changes is
    # the Arabic display string.
    _cache: dict[str, "RefreshStatus"] = {}

    def __str__(self) -> str:
        return f"{self.code} ({self.label})"

    @classmethod
    def get_by_code(cls, code: str) -> "RefreshStatus":
        if code not in cls._cache:
            cls._cache[code] = cls.objects.get(code=code)
        return cls._cache[code]

    @classmethod
    def clear_cache(cls) -> None:
        cls._cache.clear()

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        type(self).clear_cache()

    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        type(self).clear_cache()
        return result


class MatchType(models.TextChoices):
    # ``pattern`` is a substring; matches if it occurs anywhere in the raw text body.
    CONTAINS = "contains", "Contains (text)"
    # ``pattern`` is a Python regex evaluated against the raw text body.
    REGEX = "regex", "Regex (text)"
    # ``pattern`` is a JSONPath (e.g. ``$.StatusCode``) and ``expected_value``
    # is the value the extracted node must equal (compared as string after
    # stripping; numeric equality is also tried).
    JSON_PATH = "json_path", "JSON Path (equals)"
    # Like JSON_PATH, but ``expected_value`` is treated as a substring that
    # must occur inside the extracted string value. Handy for matching on a
    # message field whose exact phrasing varies (e.g. Aloha's $.message).
    JSON_PATH_CONTAINS = "json_path_contains", "JSON Path (contains)"
    # ``expected_value`` is an integer that must equal the HTTP status code.
    STATUS_CODE = "status_code", "HTTP Status"


class ProviderResponseRule(models.Model):
    """Configurable rule that maps a provider response shape to a ``RefreshStatus``.

    Rules are evaluated in (provider, order, id) order; the first match wins.
    """

    provider = models.CharField(
        max_length=20,
        choices=PhoneProvider.choices,
        db_index=True,
    )
    match_type = models.CharField(max_length=20, choices=MatchType.choices)
    pattern = models.CharField(
        max_length=500,
        help_text=_("Substring, regex, or JSON path expression depending on match type."),
    )
    expected_value = models.CharField(
        max_length=200,
        blank=True,
        help_text=_("For JSON path / HTTP status: the value to compare against."),
    )
    target_status = models.ForeignKey(
        RefreshStatus,
        on_delete=models.PROTECT,
        related_name="rules",
    )
    order = models.PositiveIntegerField(
        default=0,
        help_text=_("Lower runs first; first match wins."),
    )
    is_active = models.BooleanField(default=True)
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["provider", "order", "id"]
        indexes = [models.Index(fields=["provider", "is_active", "order"])]

    def __str__(self):
        return f"{self.provider}/{self.order}: {self.match_type} → {self.target_status_id}"


class CustomerMessage(models.Model):
    """Customer-facing message shown for each refresh outcome.

    One row per ``RefreshStatus`` (enforced by the one-to-one FK); seeded
    by the initial data migration.
    """

    status = models.OneToOneField(
        RefreshStatus,
        on_delete=models.PROTECT,
        related_name="message",
    )
    title = models.CharField(max_length=200, blank=True)
    body = models.TextField(help_text=_("Message shown to the customer (Arabic)."))
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["status__sort_order", "status__id"]

    def __str__(self):
        return f"{self.status.label}: {self.title or self.body[:40]}"


class RefreshSource(models.TextChoices):
    """Where a :class:`RefreshLog` row was triggered from.

    ``LEGACY`` is reserved for rows that existed before source tracking
    was introduced — it is backfilled by the 0011 migration and MUST
    NEVER be written by application code for new rows.
    """

    API = "api", "API"
    WEB = "web", "Web"
    EMPLOYEE = "employee", "المبيعات"
    INTERNAL_TEST = "internal_test", "Internal Test"
    LEGACY = "legacy", "Legacy"


class RefreshLog(models.Model):
    """Audit log of every refresh attempt (one row per request)."""

    phone = models.CharField(max_length=20, db_index=True)
    provider = models.CharField(
        max_length=20,
        choices=PhoneProvider.choices,
        blank=True,
    )
    source = models.CharField(
        max_length=20,
        choices=RefreshSource.choices,
        default=RefreshSource.WEB,
        db_index=True,
        help_text=_("Where the refresh attempt was triggered from."),
    )
    status = models.ForeignKey(
        RefreshStatus,
        on_delete=models.PROTECT,
        related_name="log_entries",
    )
    raw_status_code = models.IntegerField(null=True, blank=True)
    raw_excerpt = models.TextField(
        blank=True,
        help_text=_("First ~500 chars of the raw upstream response (for debugging)."),
    )
    # Full upstream response body (truncated to a sane upper bound in the
    # service layer — see ``MAX_RAW_BODY_CHARS`` in
    # ``services.refresh_service``). Kept separate from ``raw_excerpt`` so
    # older rows render with an empty placeholder instead of breaking, and
    # so the listing query can stay cheap by deferring this column.
    raw_body = models.TextField(blank=True, default="")
    matched_rule = models.ForeignKey(
        ProviderResponseRule,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="logs",
    )
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["provider", "-created_at"]),
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.phone} [{self.provider or '-'}] {self.status_id} @ {self.created_at:%Y-%m-%d %H:%M}"


class SystemSettings(models.Model):
    """Singleton row holding global toggles for the refresh service.

    There's always exactly one row at ``pk=1``; use :meth:`get` to fetch
    it (creates it on first access). The :meth:`save` override pins the
    primary key to ``1`` so any callers that skip ``get`` can't accidentally
    create a second row.
    """

    service_enabled = models.BooleanField(
        default=True,
        help_text=_("When off, the public refresh API/page returns the SERVICE_OFF message."),
    )
    db_precheck_enabled = models.BooleanField(
        default=True,
        help_text=_(
            "When on, look up the phone in sales_sale.reference_number to choose the provider. "
            "When off, every refresh routes through ``default_provider``."
        ),
    )
    show_last_refresh = models.BooleanField(
        default=True,
        help_text=_("Include last_refresh_at / seconds_since_last_refresh in the public API response."),
    )
    cooldown_seconds = models.PositiveIntegerField(
        default=6 * 60 * 60,
        help_text=_("Per-phone cooldown between successful refreshes (in seconds)."),
    )
    default_provider = models.CharField(
        max_length=20,
        choices=PhoneProvider.choices,
        blank=True,
        help_text=_("Used when DB precheck is disabled. Empty → return ERROR."),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "System settings"
        verbose_name_plural = "System settings"

    def __str__(self):
        return "System settings"

    def save(self, *args, **kwargs):
        # Pin the primary key so the table can never grow past one row.
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):  # pragma: no cover — guard rail
        return  # singleton is never deleted

    @classmethod
    def get(cls) -> "SystemSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class ApiSettings(models.Model):
    """Singleton row holding configuration for the public refresh API.

    Mirrors :class:`SystemSettings` (one row pinned at ``pk=1``); kept
    separate so admins can change auth/rate-limit knobs without touching
    the customer-facing service toggles.
    """

    require_token = models.BooleanField(
        default=False,
        help_text=_(
            "When ON, the public API endpoint requires a valid "
            "Authorization: Bearer <token> header."
        ),
    )
    rate_limit_per_minute = models.PositiveIntegerField(
        default=60,
        help_text=_("Max public API requests per IP per minute."),
    )
    rate_limit_per_hour = models.PositiveIntegerField(
        default=600,
        help_text=_("Max public API requests per IP per hour."),
    )
    allow_anonymous_test_page = models.BooleanField(
        default=False,
        help_text=_(
            "When ON, the public /phone-refresh/ form remains accessible "
            "without a token even when require_token is ON."
        ),
    )
    allowed_origins = models.TextField(
        blank=True,
        help_text=_("One origin per line. Empty = allow any origin."),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "API settings"
        verbose_name_plural = "API settings"

    def __str__(self):
        return "API settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):  # pragma: no cover — guard rail
        return  # singleton is never deleted

    @classmethod
    def get(cls) -> "ApiSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def allowed_origins_list(self) -> list[str]:
        return [
            line.strip()
            for line in (self.allowed_origins or "").splitlines()
            if line.strip()
        ]


class ApiToken(models.Model):
    """Bearer token for the public refresh API.

    The raw token is shown to the admin once on creation; only its
    sha256 hash and the first 8 characters (``prefix``) are persisted.
    Authentication compares ``sha256(received_token)`` against
    ``token_hash``; ``last_used_at`` is bumped on every successful auth
    for a low-fidelity audit trail.
    """

    name = models.CharField(
        max_length=120,
        help_text=_("Friendly label for this token (e.g. where it's used)."),
    )
    token_hash = models.CharField(
        max_length=64,
        unique=True,
        help_text=_("sha256 of the raw token; raw value is shown only once on creation."),
    )
    prefix = models.CharField(
        max_length=10,
        help_text=_("First 8 chars of the raw token, for identification in lists."),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="phone_refresh_api_tokens",
    )
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "API token"
        verbose_name_plural = "API tokens"

    def __str__(self):
        state = "revoked" if self.revoked_at else "active"
        return f"{self.name} ({self.prefix}…, {state})"

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


class ProviderConfig(models.Model):
    """Per-provider on/off toggle.

    One row per ``PhoneProvider`` key (``sky``, ``aloha``, ``layan``,
    ``areen``) seeded by migration 0007. The refresh orchestrator
    consults :meth:`is_provider_enabled` after resolving a provider for
    a phone number; when the row is ``is_enabled=False`` the public
    flow short-circuits with the ``provider_off`` status. Admin tests
    can bypass this gate with ``bypass_provider_off=True``.
    """

    provider = models.CharField(
        max_length=20,
        choices=PhoneProvider.choices,
        unique=True,
        db_index=True,
    )
    is_enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["provider"]
        verbose_name = "Provider config"
        verbose_name_plural = "Provider configs"

    def __str__(self) -> str:
        return f"{self.provider} ({'on' if self.is_enabled else 'off'})"

    @classmethod
    def is_provider_enabled(cls, provider_key: str) -> bool:
        """Return True when ``provider_key`` is enabled (default ON)."""
        if not provider_key:
            return True
        cfg = cls.objects.filter(provider=provider_key).first()
        return cfg.is_enabled if cfg else True


# Flask ``refresh_numbers`` parity defaults (see app.py SOCIAL_* constants).
DEFAULT_SOCIAL_WHATSAPP_URL = "https://wa.me/972555544071"
DEFAULT_SOCIAL_FACEBOOK_URL = (
    "https://www.facebook.com/profile.php?id=61561099095296"
)


class SiteSettings(models.Model):
    """Singleton row holding host/subdomain routing for the public refresh page.

    When :attr:`public_subdomain` is set, the
    ``PhoneRefreshSubdomainMiddleware`` serves ONLY the public refresh
    experience on that host and 404s every other path (admin, login,
    management). When :attr:`redirect_main_to_subdomain` is on, hits to
    ``/phone-refresh/`` on the main host are 302-redirected to the
    configured subdomain.

    Always exactly one row at ``pk=1`` — use :meth:`get_solo` to fetch it.
    """

    public_subdomain = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="مثال: rn.prosim.ps — اتركه فارغاً لتعطيل التوجيه عبر سب دومين.",
    )
    redirect_main_to_subdomain = models.BooleanField(
        default=False,
        help_text="عند التفعيل: زيارة /phone-refresh/ على الدومين الرئيسي يحوّل تلقائياً إلى السب دومين.",
    )
    whatsapp_url = models.CharField(
        max_length=255,
        blank=True,
        default=DEFAULT_SOCIAL_WHATSAPP_URL,
        help_text=(
            "رابط WhatsApp كامل (مثال: https://wa.me/970599999999). "
            "اتركه فارغاً لإخفاء الأيقونة."
        ),
    )
    facebook_url = models.CharField(
        max_length=255,
        blank=True,
        default=DEFAULT_SOCIAL_FACEBOOK_URL,
        help_text="رابط صفحة Facebook كامل. اتركه فارغاً لإخفاء الأيقونة.",
    )
    public_page_token = models.ForeignKey(
        ApiToken,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="التوكن المستخدم لمصادقة طلبات صفحة التحديث العامة.",
    )
    public_page_token_raw = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="القيمة الخام للتوكن (تُحقَن في الصفحة العامة عند التحميل).",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "إعدادات الموقع"
        verbose_name_plural = "إعدادات الموقع"

    def __str__(self) -> str:
        return "Site settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
        from phone_refresh.middleware import clear_site_settings_cache

        clear_site_settings_cache()

    def delete(self, *args, **kwargs):  # pragma: no cover — guard rail
        return  # singleton is never deleted

    @classmethod
    def get_solo(cls) -> "SiteSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
