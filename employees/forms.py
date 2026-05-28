from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from employees.models import EmployeeProfile

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
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
    notes = forms.CharField(
        label=_("Notes"),
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount == Decimal("0"):
            raise forms.ValidationError(_("Amount cannot be zero."))
        return amount
