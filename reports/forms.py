from django import forms
from django.utils.translation import gettext_lazy as _


class StalePhoneThresholdDaysForm(forms.Form):
    stale_phone_threshold_days = forms.IntegerField(
        label=_("Days without a new sale entry"),
        min_value=1,
        max_value=3650,
    )


class StalePhoneLineEditForm(forms.Form):
    sim_identifier = forms.CharField(
        label=_("SIM / chip"),
        required=False,
        max_length=200,
        widget=forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
    )
