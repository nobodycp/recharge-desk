from django import forms
from django.db.models import Min
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _

from companies.models import Company, ProductLine


class StalePhoneLineEditForm(forms.Form):
    """Inline / page form for ICCID + notes on a tracked line."""

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
    notes = forms.CharField(
        label=_("Notes"),
        required=False,
        max_length=500,
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-sm",
                "autocomplete": "off",
                "placeholder": _("Note…"),
            }
        ),
    )


class PhoneLineFullEditForm(StalePhoneLineEditForm):
    """Edit page: change the phone/reference (resets disconnect countdown)."""

    reference_number = forms.CharField(
        label=_("Number / reference"),
        max_length=64,
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-sm font-monospace",
                "autocomplete": "off",
                "inputmode": "tel",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Put the number first on the edit page.
        self.order_fields(["reference_number", "sim_identifier", "notes"])

    def clean_reference_number(self):
        raw = (self.cleaned_data.get("reference_number") or "").strip()
        if not raw:
            raise forms.ValidationError(_("Enter a phone number or reference."))
        return raw


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
            ("ended_under_threshold", _("Disconnected only")),
            ("overdue", _("Left the agency")),
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
        label=_("Days to leave the agency"),
        min_value=1,
        max_value=3650,
        required=False,
        help_text=_(
            "Days after subscription end before the line left the agency. "
            "Example: 60-day charge + 85 days → left after 145 days. Saved on your profile."
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
