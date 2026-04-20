from django import forms
from django.utils.translation import gettext_lazy as _

from core.models import SiteBranding


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
