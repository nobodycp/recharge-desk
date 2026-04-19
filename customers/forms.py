from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _

from customers.models import Customer
from sales.models import PaymentMethod


class CustomerForm(forms.ModelForm):
    """Create / edit a customer record (manager-side)."""

    initial_phone = forms.CharField(
        label=_("Phone or shipment number"),
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "autocomplete": "off"}
        ),
        help_text=_("Optional. Adds a first phone link so future sales auto-suggest this customer."),
    )

    class Meta:
        model = Customer
        fields = ["name", "notes", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }


class CustomerPhoneForm(forms.Form):
    phone = forms.CharField(
        label=_("Phone or shipment number"),
        max_length=64,
        widget=forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
    )
    label = forms.CharField(
        label=_("Label"),
        max_length=80,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )


class CustomerPaymentForm(forms.Form):
    amount = forms.DecimalField(
        label=_("Amount"),
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0.01"}),
    )
    payment_method = forms.ModelChoiceField(
        label=_("Payment method"),
        queryset=PaymentMethod.objects.filter(is_active=True).order_by("name"),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    notes = forms.CharField(
        label=_("Notes"),
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )


class CustomerAdjustmentForm(forms.Form):
    """Manual balance adjustment that doesn't touch sales / profit / loss."""

    KIND_DEBT = "debt"
    KIND_CREDIT = "credit"
    KIND_CHOICES = (
        (KIND_DEBT, _("Add as debt (customer owes more)")),
        (KIND_CREDIT, _("Add as credit (reduce balance)")),
    )

    kind = forms.ChoiceField(
        label=_("Adjustment type"),
        choices=KIND_CHOICES,
        initial=KIND_DEBT,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    amount = forms.DecimalField(
        label=_("Amount"),
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0.01"}),
    )
    notes = forms.CharField(
        label=_("Notes"),
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        help_text=_("Why this adjustment? Shown in the ledger."),
    )

    def signed_amount(self) -> Decimal:
        amt = Decimal(self.cleaned_data["amount"])
        return amt if self.cleaned_data["kind"] == self.KIND_DEBT else -amt
