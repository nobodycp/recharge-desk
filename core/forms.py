from django import forms
from django.utils.translation import gettext_lazy as _

from core.models import AppSettings, SiteBranding


class AppSettingsForm(forms.ModelForm):
    class Meta:
        model = AppSettings
        fields = [
            "allow_sales_auto_create_customer",
            "default_language",
            "default_theme",
            "public_default_language",
            "public_default_theme",
        ]
        widgets = {
            "allow_sales_auto_create_customer": forms.CheckboxInput(
                attrs={"class": "form-check-input", "role": "switch"}
            ),
            "default_language": forms.Select(attrs={"class": "form-select"}),
            "default_theme": forms.Select(attrs={"class": "form-select"}),
            "public_default_language": forms.Select(attrs={"class": "form-select"}),
            "public_default_theme": forms.Select(attrs={"class": "form-select"}),
        }
        labels = {
            "allow_sales_auto_create_customer": _("Create customers from sales entry"),
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
