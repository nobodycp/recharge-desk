from django import forms
from django.utils.translation import gettext_lazy as _

from core.models import AppSettings, SiteBranding

_SWITCH = forms.CheckboxInput(attrs={"class": "form-check-input", "role": "switch"})


class AppSettingsForm(forms.ModelForm):
    class Meta:
        model = AppSettings
        fields = [
            "require_debt_request_approval",
            "require_settlement_request_approval",
            "require_payment_request_approval",
            "allow_sales_auto_create_customer",
            "sales_inventory_enabled",
            "sales_show_refresh_phone",
            "sales_show_record_payment",
            "sales_show_employee_payment",
            "default_language",
            "default_theme",
            "public_default_language",
            "public_default_theme",
        ]
        widgets = {
            "require_debt_request_approval": _SWITCH,
            "require_settlement_request_approval": _SWITCH,
            "require_payment_request_approval": _SWITCH,
            "allow_sales_auto_create_customer": _SWITCH,
            "sales_inventory_enabled": _SWITCH,
            "sales_show_refresh_phone": _SWITCH,
            "sales_show_record_payment": _SWITCH,
            "sales_show_employee_payment": _SWITCH,
            "default_language": forms.Select(attrs={"class": "form-select"}),
            "default_theme": forms.Select(attrs={"class": "form-select"}),
            "public_default_language": forms.Select(attrs={"class": "form-select"}),
            "public_default_theme": forms.Select(attrs={"class": "form-select"}),
        }
        labels = {
            "require_debt_request_approval": _("Debt requests require approval"),
            "require_settlement_request_approval": _("Settlement requests require approval"),
            "require_payment_request_approval": _("Payment requests require approval"),
            "allow_sales_auto_create_customer": _("Create customers from sales entry"),
            "sales_inventory_enabled": _("Inventory (New SIM) on sales entry"),
            "sales_show_refresh_phone": _("Phone refresh button on sales entry"),
            "sales_show_record_payment": _("Record payment button on sales entry"),
            "sales_show_employee_payment": _("Payment to employee button on sales entry"),
            "default_language": _("Default language"),
            "default_theme": _("Default theme"),
            "public_default_language": _("Refresh link default language"),
            "public_default_theme": _("Refresh link default theme"),
        }


class SiteBrandingForm(forms.ModelForm):
    class Meta:
        model = SiteBranding
        fields = ["site_name", "tagline", "logo", "favicon"]
        widgets = {
            "site_name": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "maxlength": 120,
                    "placeholder": _("e.g. Recharge Desk"),
                }
            ),
            "tagline": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "maxlength": 160,
                    "placeholder": _("e.g. Management"),
                }
            ),
            "logo": forms.ClearableFileInput(attrs={"class": "form-control", "accept": "image/*"}),
            "favicon": forms.ClearableFileInput(attrs={"class": "form-control", "accept": "image/*"}),
        }
        labels = {
            "site_name": _("Site name"),
            "tagline": _("Sidebar tagline"),
            "logo": _("Site logo"),
            "favicon": _("Browser tab icon"),
        }