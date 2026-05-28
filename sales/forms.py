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
        required=False,
        widget=forms.HiddenInput(attrs={"id": "id_payment_method"}),
    )
    on_account = forms.BooleanField(
        label=_("On account"),
        required=False,
        widget=forms.HiddenInput(attrs={"id": "id_on_account"}),
    )
    paid_via_employee = forms.BooleanField(
        label=_("Payment to employee"),
        required=False,
        widget=forms.HiddenInput(attrs={"id": "id_paid_via_employee"}),
    )
    employee_recipient = forms.ModelChoiceField(
        label=_("Employee"),
        queryset=None,
        required=False,
        widget=forms.HiddenInput(attrs={"id": "id_employee_recipient"}),
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
    is_new_sim = forms.BooleanField(
        label="",
        required=False,
        initial=False,
        widget=forms.CheckboxInput(
            attrs={
                "id": "id_is_new_sim",
                "class": "form-check-input",
            }
        ),
    )
    sim_serial_or_iccid = forms.CharField(
        label=_("SIM serial or ICCID"),
        required=False,
        max_length=64,
        widget=forms.TextInput(
            attrs={
                "id": "id_sim_serial_or_iccid",
                "class": "form-control form-control-sm",
                "placeholder": _("Optional"),
                "autocomplete": "off",
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
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        qs = Product.objects.filter(is_active=True).select_related("line", "line__company")
        if company_id:
            qs = qs.filter(line__company_id=company_id)
        self.fields["product"].queryset = qs
        from employees.models import EmployeeProfile
        from employees.services import get_acting_employee_profile

        self.acting_employee = get_acting_employee_profile(self.user)
        self.fields["employee_recipient"].queryset = EmployeeProfile.objects.filter(
            is_active=True
        ).select_related("user", "user__profile")

    def clean(self):
        cleaned = super().clean()
        company = cleaned.get("company")
        product = cleaned.get("product")
        if company and product and product.line.company_id != company.id:
            raise forms.ValidationError(_("Selected product does not belong to the company."))
        on_account = bool(cleaned.get("on_account"))
        paid_via_employee = bool(cleaned.get("paid_via_employee"))
        payment_method = cleaned.get("payment_method")
        if on_account and paid_via_employee:
            raise forms.ValidationError(
                _("Choose either on-account or payment to employee, not both.")
            )
        if paid_via_employee:
            cleaned["payment_method"] = None
            if not self.acting_employee:
                raise forms.ValidationError(
                    _("You are not registered as a payroll employee.")
                )
            cleaned["employee_recipient"] = self.acting_employee
        elif on_account:
            if payment_method is not None:
                cleaned["payment_method"] = None
            cleaned["employee_recipient"] = None
        else:
            cleaned["employee_recipient"] = None
            if payment_method is None:
                self.add_error("payment_method", _("Pick a payment method."))
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


class EmployeeRecentFilterForm(forms.Form):
    """Search/filter form for the employee 'My entries' page.

    Every field is optional. When the form is submitted blank the view
    falls back to "today only" so the employee never accidentally pulls
    their entire history. The form itself only validates input — it
    never reaches the database directly.
    """

    q = forms.CharField(
        label=_("Search"),
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-sm",
                "placeholder": _("Phone, shipment number, payer name…"),
                "autocomplete": "off",
            }
        ),
    )
    company = forms.ModelChoiceField(
        label=_("Company"),
        queryset=Company.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    payment_method = forms.ModelChoiceField(
        label=_("Payment method"),
        queryset=PaymentMethod.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    status = forms.ChoiceField(
        label=_("Status"),
        choices=[("", _("All"))] + list(Sale.Status.choices),
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    date_from = forms.DateField(
        label=_("Date from"),
        required=False,
        widget=forms.DateInput(
            attrs={"class": "form-control form-control-sm", "type": "date"}
        ),
    )
    date_to = forms.DateField(
        label=_("Date to"),
        required=False,
        widget=forms.DateInput(
            attrs={"class": "form-control form-control-sm", "type": "date"}
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Keep dropdowns short and relevant — the employee shouldn't be
        # offered companies / payment methods that have been retired.
        self.fields["company"].queryset = Company.objects.filter(is_active=True).order_by("name")
        self.fields["payment_method"].queryset = (
            PaymentMethod.objects.filter(is_active=True).order_by("name")
        )

    def clean(self):
        cleaned = super().clean()
        df = cleaned.get("date_from")
        dt = cleaned.get("date_to")
        if df and dt and df > dt:
            raise forms.ValidationError(_("'Date from' must be on or before 'Date to'."))
        return cleaned

    def has_any_filter(self) -> bool:
        """True iff the user submitted at least one non-empty field.

        Used by the view to decide whether to default to "today only" or
        honour the (possibly empty) explicit submission.
        """
        if not self.is_bound or not self.is_valid():
            return False
        return any(self.cleaned_data.get(name) for name in self.fields)


class PaymentMethodForm(forms.ModelForm):
    class Meta:
        model = PaymentMethod
        fields = ["name", "icon", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "icon": forms.ClearableFileInput(attrs={"class": "form-control"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }


class ManagementSaleEditForm(forms.ModelForm):
    """Edit safe fields on an existing sale from the management UI.

    Excludes company / product / is_esim because those would invalidate
    the supplier-balance ledger snapshot taken at creation time.
    """

    class Meta:
        model = Sale
        fields = [
            "payment_method",
            "payer_name",
            "reference_number",
            "sell_price_actual",
            "notes",
        ]
        widgets = {
            "payment_method": forms.Select(attrs={"class": "form-select"}),
            "payer_name": forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
            "reference_number": forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
            "sell_price_actual": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.01", "min": "0"}
            ),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["payment_method"].queryset = (
            PaymentMethod.objects.filter(is_active=True).order_by("name")
        )


class EmployeeSaleEditForm(ManagementSaleEditForm):
    """Employee edit form — hides payment method for on-account / employee payments."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        sale = self.instance
        if sale and (sale.paid_via_employee or sale.on_account):
            del self.fields["payment_method"]

    def clean(self):
        cleaned = super().clean()
        sale = self.instance
        if sale and not sale.paid_via_employee and not sale.on_account:
            if not cleaned.get("payment_method"):
                self.add_error("payment_method", _("Pick a payment method."))
        return cleaned


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
