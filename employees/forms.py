from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from employees.models import EmployeeLedgerEntry, EmployeeProfile

User = get_user_model()


class EmployeeProfileForm(forms.ModelForm):
    user = forms.ModelChoiceField(
        label=_("User"),
        queryset=User.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    class Meta:
        model = EmployeeProfile
        fields = ("user", "monthly_salary", "is_active")
        widgets = {
            "monthly_salary": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.01", "min": "0"}
            ),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        used_ids = EmployeeProfile.objects.values_list("user_id", flat=True)
        qs = User.objects.filter(is_active=True).order_by("username")
        if self.instance.pk:
            qs = qs.filter(Q(pk=self.instance.user_id) | ~Q(pk__in=used_ids))
        else:
            qs = qs.exclude(pk__in=used_ids)
        self.fields["user"].queryset = qs


class EmployeeAdjustmentForm(forms.Form):
    amount = forms.DecimalField(
        label=_("Amount"),
        max_digits=14,
        decimal_places=2,
        help_text=_("Positive credits the employee; negative debits them."),
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01"}),
    )
    notes = forms.CharField(
        label=_("Notes"),
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control form-control-sm", "placeholder": _("Optional notes")},
        ),
    )

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount == Decimal("0"):
            raise forms.ValidationError(_("Amount cannot be zero."))
        return amount


class EmployeeLedgerFilterForm(forms.Form):
    ledger_q = forms.CharField(
        label=_("Search"),
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-sm",
                "placeholder": _("Phone, payer, notes…"),
                "autocomplete": "off",
            }
        ),
    )
    ledger_entry_type = forms.ChoiceField(
        label=_("Type"),
        choices=[("", _("All"))] + list(EmployeeLedgerEntry.EntryType.choices),
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    ledger_date_from = forms.DateField(
        label=_("Date from"),
        required=False,
        widget=forms.DateInput(
            attrs={"class": "form-control form-control-sm", "type": "date"}
        ),
    )
    ledger_date_to = forms.DateField(
        label=_("Date to"),
        required=False,
        widget=forms.DateInput(
            attrs={"class": "form-control form-control-sm", "type": "date"}
        ),
    )

    def clean(self):
        cleaned = super().clean()
        df = cleaned.get("ledger_date_from")
        dt = cleaned.get("ledger_date_to")
        if df and dt and df > dt:
            raise forms.ValidationError(_("'Date from' must be on or before 'Date to'."))
        return cleaned


class EmployeeSalesPaymentFilterForm(forms.Form):
    payments_q = forms.CharField(
        label=_("Search"),
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-sm",
                "placeholder": _("Phone, payer, notes…"),
                "autocomplete": "off",
            }
        ),
    )
    payments_date_from = forms.DateField(
        label=_("Date from"),
        required=False,
        widget=forms.DateInput(
            attrs={"class": "form-control form-control-sm", "type": "date"}
        ),
    )
    payments_date_to = forms.DateField(
        label=_("Date to"),
        required=False,
        widget=forms.DateInput(
            attrs={"class": "form-control form-control-sm", "type": "date"}
        ),
    )

    def clean(self):
        cleaned = super().clean()
        df = cleaned.get("payments_date_from")
        dt = cleaned.get("payments_date_to")
        if df and dt and df > dt:
            raise forms.ValidationError(_("'Date from' must be on or before 'Date to'."))
        return cleaned
