from django import forms
from django.db.models import Min
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _

from companies.models import Company, ProductLine


class StalePhoneLineEditForm(forms.Form):
    sim_identifier = forms.CharField(
        label=_("ICCID / SIM"),
        required=False,
        max_length=200,
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-sm",
                "autocomplete": "off",
                "inputmode": "text",
                "placeholder": _("ICCID or SIM serial"),
            }
        ),
    )


class PhoneLinesFilterForm(forms.Form):
    """GET filters for بيان الأرقام, including the activity threshold setting."""

    q = forms.CharField(
        label=_("Search"),
        required=False,
        widget=forms.TextInput(
            attrs={
                "id": "phone-lines-filter-q",
                "class": "form-control form-control-sm",
                "placeholder": _("Reference, payer, line, ICCID…"),
                "autocomplete": "off",
            }
        ),
    )
    company = forms.ModelChoiceField(
        label=_("Company"),
        queryset=Company.objects.filter(is_active=True).order_by("name"),
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    line_name = forms.ChoiceField(
        label=_("Line"),
        required=False,
        choices=(),
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
        help_text=_("Same line name across companies (e.g. all Silicom)."),
    )
    status = forms.ChoiceField(
        label=_("Status"),
        required=False,
        choices=(
            ("", "---------"),
            ("active", _("Active lines")),
            ("due_soon", _("Ends today")),
            ("ended_under_threshold", _("Ended — under inactivity threshold")),
            ("overdue", _("Ended — past inactivity threshold")),
        ),
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    sim_type = forms.ChoiceField(
        label=_("SIM type"),
        required=False,
        choices=(
            ("", "---------"),
            ("physical", _("Physical SIM")),
            ("esim", _("eSIM")),
        ),
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    threshold_days = forms.IntegerField(
        label=_("Activity threshold (days)"),
        min_value=1,
        max_value=3650,
        required=False,
        help_text=_(
            "Idle days after subscription end. Example: 60-day charge + 85-day "
            "threshold → past threshold after 145 days. Saved on your profile."
        ),
        widget=forms.NumberInput(
            attrs={"class": "form-control form-control-sm", "min": "1"}
        ),
    )

    def __init__(self, *args, default_threshold: int = 85, **kwargs):
        super().__init__(*args, **kwargs)
        rows = (
            ProductLine.objects.annotate(name_key=Lower("name"))
            .values("name_key")
            .annotate(label=Min("name"))
            .order_by("label")
        )
        self.fields["line_name"].choices = [("", "---------")] + [
            (r["name_key"], r["label"]) for r in rows if r["name_key"]
        ]
        self.fields["threshold_days"].initial = max(1, int(default_threshold or 85))
