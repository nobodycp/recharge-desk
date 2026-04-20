from django import forms
from django.utils.translation import gettext_lazy as _

from core.models import SiteBranding


class SiteBrandingForm(forms.ModelForm):
    class Meta:
        model = SiteBranding
        fields = ["logo", "favicon"]
        widgets = {
            "logo": forms.ClearableFileInput(attrs={"class": "form-control", "accept": "image/*"}),
            "favicon": forms.ClearableFileInput(attrs={"class": "form-control", "accept": "image/*"}),
        }
        labels = {
            "logo": _("Site logo"),
            "favicon": _("Browser tab icon"),
        }
