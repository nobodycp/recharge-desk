from django import forms
from django.utils.translation import gettext_lazy as _

from companies.models import Company, Product


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


class StalePhonesFilterForm(forms.Form):
    """GET filters for the idle-lines report (subset of the management sales filter bar)."""

    q = forms.CharField(
        label=_("Search"),
        required=False,
        widget=forms.TextInput(
            attrs={
                "id": "stale-filter-q",
                "class": "form-control form-control-sm",
                "placeholder": _("Reference, payer, company…"),
                "autocomplete": "off",
            }
        ),
    )
    company = forms.ModelChoiceField(
        label=_("Company"),
        queryset=Company.objects.all(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    product = forms.ModelChoiceField(
        label=_("Product"),
        queryset=Product.objects.select_related("line").all(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.select_related(
            "line", "line__company"
        ).order_by("line__company__name", "line__sort_order", "line__name", "variant_label")
