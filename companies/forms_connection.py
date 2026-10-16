"""Forms for the company page "الربط مع المزوّد" tab."""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from companies.models import Company
from providers.contracts import Capability
from providers.models import ProviderConnection
from providers.registry import ADAPTERS

SERVICE_LABELS = {
    Capability.REFRESH: _("تحديث الأرقام"),
    Capability.NUMBER_INFO: _("فحص الرقم"),
    Capability.CHARGE: _("الشحن"),
    Capability.RECONCILE: _("مطابقة الحسابات"),
}


class CompanySystemForm(forms.ModelForm):
    """Which provider system runs this company's services."""

    class Meta:
        model = Company
        fields = ["provider_key"]
        labels = {"provider_key": _("نظام الشركة")}
        widgets = {"provider_key": forms.Select(attrs={"class": "form-select"})}


class ConnectionServicesForm(forms.ModelForm):
    """Master switch and per-service switches of one provider connection.

    A service the adapter does not support keeps its stored value: its switch is
    disabled, so a posted value is ignored and can never turn it on.
    """

    class Meta:
        model = ProviderConnection
        fields = [
            "is_enabled",
            "refresh_enabled",
            "number_info_enabled",
            "charge_enabled",
            "reconcile_enabled",
        ]
        labels = {"is_enabled": _("تفعيل الربط مع المزوّد")}
        widgets = {
            name: forms.CheckboxInput(
                attrs={"class": "form-check-input", "role": "switch"}
            )
            for name in fields
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        supported = ADAPTERS[self.instance.provider_key].capabilities
        for capability, field_name in ProviderConnection.SERVICE_FIELDS.items():
            self.fields[field_name].label = SERVICE_LABELS[capability]
            if capability not in supported:
                self.fields[field_name].disabled = True
