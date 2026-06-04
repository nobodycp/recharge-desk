from django.contrib import admin

from sms_gateway.models import (
    InboundSms,
    OutboundSms,
    SmsAccessRule,
    SmsGatewayDevice,
    SmsGatewaySettings,
    SmsReplyPolicy,
)


@admin.register(SmsGatewayDevice)
class SmsGatewayDeviceAdmin(admin.ModelAdmin):
    list_display = ("name", "phone_number", "priority", "is_active", "can_send", "last_seen_at")
    list_filter = ("is_active", "can_send")
    search_fields = ("name", "phone_number")


@admin.register(SmsAccessRule)
class SmsAccessRuleAdmin(admin.ModelAdmin):
    list_display = ("value", "mode", "is_active")
    list_filter = ("mode", "is_active")
    search_fields = ("value",)


@admin.register(InboundSms)
class InboundSmsAdmin(admin.ModelAdmin):
    list_display = ("from_number", "extracted_number", "state", "received_at")
    list_filter = ("state",)
    search_fields = ("from_number", "extracted_number")


@admin.register(OutboundSms)
class OutboundSmsAdmin(admin.ModelAdmin):
    list_display = ("to_number", "state", "attempts", "created_at", "sent_at")
    list_filter = ("state",)
    search_fields = ("to_number",)


admin.site.register(SmsGatewaySettings)
admin.site.register(SmsReplyPolicy)
