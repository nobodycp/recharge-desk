from django import forms

from sms_gateway.models import (
    SmsAccessRule,
    SmsGatewayDevice,
    SmsGatewaySettings,
)

_SWITCH = forms.CheckboxInput(attrs={"class": "form-check-input", "role": "switch"})


def _num(**attrs):
    base = {"class": "form-control", "min": "0"}
    base.update(attrs)
    return forms.NumberInput(attrs=base)


class SmsGeneralSettingsForm(forms.ModelForm):
    class Meta:
        model = SmsGatewaySettings
        fields = [
            "service_enabled",
            "replies_master_enabled",
            "send_service_off_reply",
            "service_off_message",
            "test_number",
            "test_reply_message",
            "sender_max_messages",
            "sender_window_minutes",
            "global_daily_cap",
            "allowlist_mode",
            "claim_timeout_seconds",
            "auto_pause_threshold",
            "max_send_attempts",
            "log_retention_days",
            "heartbeat_alert_minutes",
        ]
        widgets = {
            "service_enabled": _SWITCH,
            "replies_master_enabled": _SWITCH,
            "send_service_off_reply": _SWITCH,
            "allowlist_mode": _SWITCH,
            "service_off_message": forms.Textarea(attrs={"class": "form-control", "rows": 2, "dir": "auto"}),
            "test_number": forms.TextInput(attrs={"class": "form-control", "dir": "ltr", "placeholder": "0595108208"}),
            "test_reply_message": forms.Textarea(attrs={"class": "form-control", "rows": 2, "dir": "auto"}),
            "sender_max_messages": _num(),
            "sender_window_minutes": _num(),
            "global_daily_cap": _num(),
            "claim_timeout_seconds": _num(step="10"),
            "auto_pause_threshold": _num(),
            "max_send_attempts": _num(min="1"),
            "log_retention_days": _num(),
            "heartbeat_alert_minutes": _num(),
        }


class SmsApiGatewayForm(forms.ModelForm):
    class Meta:
        model = SmsGatewaySettings
        fields = [
            "refresh_api_url",
            "refresh_api_token",
            "refresh_api_timeout",
            "refresh_api_phone_field",
            "refresh_api_status_path",
            "refresh_api_title_path",
            "refresh_api_body_path",
        ]
        widgets = {
            "refresh_api_url": forms.TextInput(
                attrs={"class": "form-control", "dir": "ltr", "placeholder": "https://s.prosim.ps/phone-refresh/api/refresh/"}
            ),
            "refresh_api_token": forms.TextInput(attrs={"class": "form-control", "dir": "ltr", "autocomplete": "off"}),
            "refresh_api_timeout": _num(min="1", max="120"),
            "refresh_api_phone_field": forms.TextInput(attrs={"class": "form-control", "dir": "ltr"}),
            "refresh_api_status_path": forms.TextInput(attrs={"class": "form-control", "dir": "ltr"}),
            "refresh_api_title_path": forms.TextInput(attrs={"class": "form-control", "dir": "ltr"}),
            "refresh_api_body_path": forms.TextInput(attrs={"class": "form-control", "dir": "ltr"}),
        }


class SmsDeviceForm(forms.ModelForm):
    class Meta:
        model = SmsGatewayDevice
        fields = ["name", "phone_number", "priority", "is_active", "can_send", "daily_send_limit", "notes"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "dir": "auto", "placeholder": "الجهاز الرئيسي"}),
            "phone_number": forms.TextInput(attrs={"class": "form-control", "dir": "ltr", "placeholder": "0595108208"}),
            "priority": _num(),
            "is_active": _SWITCH,
            "can_send": _SWITCH,
            "daily_send_limit": _num(),
            "notes": forms.TextInput(attrs={"class": "form-control", "dir": "auto"}),
        }

    def clean_name(self) -> str:
        value = (self.cleaned_data.get("name") or "").strip()
        if not value:
            raise forms.ValidationError("الاسم مطلوب.")
        return value


class SmsAccessRuleForm(forms.ModelForm):
    class Meta:
        model = SmsAccessRule
        fields = ["value", "mode", "note"]
        widgets = {
            "value": forms.TextInput(attrs={"class": "form-control", "dir": "ltr", "placeholder": "0595... أو بادئة"}),
            "mode": forms.Select(attrs={"class": "form-select"}),
            "note": forms.TextInput(attrs={"class": "form-control", "dir": "auto"}),
        }

    def clean_value(self) -> str:
        value = (self.cleaned_data.get("value") or "").strip()
        if not value:
            raise forms.ValidationError("القيمة مطلوبة.")
        return value
