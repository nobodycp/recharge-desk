from django.contrib import admin

from phone_refresh.models import (
    ApiSettings,
    ApiToken,
    CustomerMessage,
    ProviderConfig,
    ProviderResponseRule,
    RefreshLog,
    RefreshStatus,
    SystemSettings,
)


@admin.register(RefreshStatus)
class RefreshStatusAdmin(admin.ModelAdmin):
    list_display = ("sort_order", "code", "label", "is_system", "updated_at")
    list_display_links = ("code",)
    list_filter = ("is_system",)
    search_fields = ("code", "label")
    ordering = ("sort_order", "id")

    def get_readonly_fields(self, request, obj=None):
        # ``code`` and ``is_system`` are part of the contract on system
        # rows; freeze them in the admin so they can only change through
        # a migration.
        if obj and obj.is_system:
            return ("code", "is_system")
        return ()

    def has_delete_permission(self, request, obj=None):
        if obj and obj.is_system:
            return False
        return super().has_delete_permission(request, obj=obj)


@admin.register(ProviderResponseRule)
class ProviderResponseRuleAdmin(admin.ModelAdmin):
    list_display = (
        "provider", "order", "match_type", "pattern", "expected_value",
        "target_status", "is_active",
    )
    list_filter = ("provider", "target_status", "match_type", "is_active")
    search_fields = ("pattern", "expected_value", "note")
    ordering = ("provider", "order", "id")
    autocomplete_fields = ("target_status",)


@admin.register(CustomerMessage)
class CustomerMessageAdmin(admin.ModelAdmin):
    list_display = ("status", "title", "updated_at")
    list_filter = ("status",)
    autocomplete_fields = ("status",)


@admin.register(SystemSettings)
class SystemSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "service_enabled",
        "db_precheck_enabled",
        "show_last_refresh",
        "cooldown_seconds",
        "default_provider",
        "updated_at",
    )

    def has_add_permission(self, request):
        return not SystemSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ApiSettings)
class ApiSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "require_token",
        "rate_limit_per_minute",
        "rate_limit_per_hour",
        "allow_anonymous_test_page",
        "updated_at",
    )

    def has_add_permission(self, request):
        return not ApiSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ApiToken)
class ApiTokenAdmin(admin.ModelAdmin):
    list_display = ("name", "prefix", "created_at", "last_used_at", "revoked_at")
    list_filter = ("revoked_at",)
    search_fields = ("name", "prefix")
    readonly_fields = ("token_hash", "prefix", "created_at", "last_used_at", "created_by")


@admin.register(ProviderConfig)
class ProviderConfigAdmin(admin.ModelAdmin):
    list_display = ("provider", "is_enabled", "updated_at")
    list_filter = ("is_enabled",)
    list_editable = ("is_enabled",)
    ordering = ("provider",)


@admin.register(RefreshLog)
class RefreshLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at", "phone", "provider", "source", "status",
        "raw_status_code", "duration_ms", "ip",
    )
    list_filter = ("provider", "source", "status")
    search_fields = ("phone", "ip", "raw_excerpt")
    date_hierarchy = "created_at"
    readonly_fields = (
        "phone",
        "provider",
        "source",
        "status",
        "raw_status_code",
        "raw_excerpt",
        "matched_rule",
        "duration_ms",
        "ip",
        "created_at",
    )
