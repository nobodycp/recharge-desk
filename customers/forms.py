from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from customers.models import Customer
from sales.models import PaymentMethod


class EmployeeCustomerPaymentSubmissionForm(forms.Form):
    """POST from the sales entry screen: record a payment for management approval."""

    customer = forms.ModelChoiceField(
        label=_("Customer"),
        queryset=Customer.objects.filter(is_active=True),
        widget=forms.HiddenInput(attrs={"id": "id_pay_sub_customer"}),
    )
    amount = forms.DecimalField(
        label=_("Amount"),
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "step": "0.01",
                "min": "0.01",
                "id": "id_pay_sub_amount",
            }
        ),
    )
    payment_method = forms.ModelChoiceField(
        label=_("Payment method"),
        queryset=PaymentMethod.objects.filter(is_active=True).order_by("name"),
        widget=forms.HiddenInput(attrs={"id": "id_pay_sub_payment_method"}),
    )
    notes = forms.CharField(
        label=_("Notes"),
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control form-control-sm",
                "rows": 2,
                "id": "id_pay_sub_notes",
            }
        ),
    )


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
    """Manual balance adjustment that doesn't touch sales / profit / loss.

    Matches :class:`sales.forms.BalanceAdjustmentForm` UX (signed amount + notes).
    """

    signed_amount = forms.DecimalField(
        label=_("Signed adjustment (+/-)"),
        max_digits=14,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
    notes = forms.CharField(
        label=_("Notes"),
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )

    def clean_signed_amount(self):
        amt = Decimal(self.cleaned_data["signed_amount"])
        if amt == 0:
            raise ValidationError(_("Adjustment amount must be non-zero."))
        return amt
