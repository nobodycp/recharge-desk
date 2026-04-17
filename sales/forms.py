from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

from companies.models import Company, Product
from sales.models import PaymentMethod, Sale

User = get_user_model()


class EmployeeSaleForm(forms.Form):
    company = forms.ModelChoiceField(
        label=_("Company"),
        queryset=Company.objects.filter(is_active=True),
        widget=forms.HiddenInput(attrs={"id": "id_company"}),
    )
    product = forms.ModelChoiceField(
        label=_("Product"),
        queryset=Product.objects.none(),
        widget=forms.HiddenInput(attrs={"id": "id_product"}),
    )
    reference_number = forms.CharField(
        label=_("Phone or shipment number"),
        max_length=64,
        widget=forms.TextInput(
            attrs={
                "id": "id_reference_number",
                "class": "form-control form-control-sm",
                "autocomplete": "off",
                "inputmode": "tel",
            }
        ),
    )
    payer_name = forms.CharField(
        label=_("Payer name"),
        max_length=200,
        widget=forms.TextInput(
            attrs={
                "id": "id_payer_name",
                "class": "form-control form-control-sm",
                "autocomplete": "off",
                "autocapitalize": "words",
                "spellcheck": "false",
            }
        ),
    )
    sell_price_actual = forms.DecimalField(
        label=_("Selling price"),
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0"),
        widget=forms.NumberInput(
            attrs={
                "class": "form-control form-control-sm",
                "step": "0.01",
                "min": "0",
                "id": "id_sell_price_actual",
            }
        ),
    )
    payment_method = forms.ModelChoiceField(
        label=_("Payment method"),
        queryset=PaymentMethod.objects.filter(is_active=True),
        widget=forms.HiddenInput(attrs={"id": "id_payment_method"}),
    )
    is_esim = forms.BooleanField(
        label="",
        required=False,
        initial=False,
        widget=forms.CheckboxInput(
            attrs={
                "id": "id_is_esim",
                "class": "form-check-input",
            }
        ),
    )
    notes = forms.CharField(
        label=_("Notes"),
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control form-control-sm", "rows": 2}),
    )

    def __init__(self, *args, **kwargs):
        company_id = kwargs.pop("company_id", None)
        super().__init__(*args, **kwargs)
        qs = Product.objects.filter(is_active=True).select_related("line", "line__company")
        if company_id:
            qs = qs.filter(line__company_id=company_id)
        self.fields["product"].queryset = qs

    def clean(self):
        cleaned = super().clean()
        company = cleaned.get("company")
        product = cleaned.get("product")
        if company and product and product.line.company_id != company.id:
            raise forms.ValidationError(_("Selected product does not belong to the company."))
        return cleaned


class ManagementSaleFilterForm(forms.Form):
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
    employee = forms.ModelChoiceField(
        label=_("Employee"),
        queryset=User.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    payment_method = forms.ModelChoiceField(
        label=_("Payment method"),
        queryset=PaymentMethod.objects.all(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    status = forms.ChoiceField(
        label=_("Status"),
        choices=[("", _("All"))] + list(Sale.Status.choices),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    date_from = forms.DateField(
        label=_("Date from"),
        required=False,
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )
    date_to = forms.DateField(
        label=_("Date to"),
        required=False,
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )
    esim = forms.ChoiceField(
        label=_("eSIM"),
        choices=[
            ("", _("All")),
            ("yes", _("eSIM only")),
            ("no", _("Non-eSIM")),
        ],
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["employee"].queryset = User.objects.filter(is_active=True).order_by("username")
        self.fields["product"].queryset = Product.objects.select_related("line", "line__company").order_by(
            "line__company__name", "line__sort_order", "line__name", "variant_label"
        )


class PaymentMethodForm(forms.ModelForm):
    class Meta:
        model = PaymentMethod
        fields = ["name", "icon", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "icon": forms.ClearableFileInput(attrs={"class": "form-control"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }


class ManualDepositForm(forms.Form):
    amount = forms.DecimalField(
        label=_("Amount"),
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
    notes = forms.CharField(
        label=_("Notes"),
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )


class BalanceAdjustmentForm(forms.Form):
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
