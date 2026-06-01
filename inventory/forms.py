from django import forms
from django.utils.translation import gettext_lazy as _

from companies.models import ProductLine
from customers.models import Customer
from inventory.line_utils import distinct_sim_product_lines


def _sim_line_queryset():
    ids = [line.pk for line in distinct_sim_product_lines()]
    return ProductLine.objects.filter(pk__in=ids).order_by("name")


def _sim_line_label(line: ProductLine) -> str:
    return line.name


class ReceiveMainStockForm(forms.Form):
    product_line = forms.ModelChoiceField(
        label=_("Product line"),
        queryset=ProductLine.objects.none(),
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    quantity = forms.IntegerField(
        label=_("Quantity"),
        min_value=1,
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm", "min": "1"}),
    )
    notes = forms.CharField(
        label=_("Notes"),
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control form-control-sm", "autocomplete": "off"}
        ),
    )
    serials = forms.CharField(
        label=_("Serial numbers (optional)"),
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control form-control-sm font-monospace rd-inv-serials-input",
                "rows": 1,
                "autocomplete": "off",
                "placeholder": _("One serial or ICCID per line; count must match quantity."),
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product_line"].queryset = _sim_line_queryset()
        self.fields["product_line"].label_from_instance = _sim_line_label


class AllocateToCustomerForm(forms.Form):
    customer = forms.ModelChoiceField(
        label=_("Customer"),
        queryset=Customer.objects.filter(is_active=True).order_by("name"),
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    product_line = forms.ModelChoiceField(
        label=_("Product line"),
        queryset=ProductLine.objects.none(),
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    quantity = forms.IntegerField(
        label=_("Quantity"),
        min_value=1,
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm", "min": "1"}),
    )
    notes = forms.CharField(
        label=_("Notes"),
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control form-control-sm", "autocomplete": "off"}
        ),
    )
    serials = forms.CharField(
        label=_("Serial numbers (optional)"),
        required=False,
        help_text=_("One serial or ICCID per line; count must match quantity."),
        widget=forms.Textarea(
            attrs={
                "class": "form-control form-control-sm font-monospace rd-inv-serials-input",
                "rows": 1,
                "autocomplete": "off",
                "placeholder": _("One per line"),
            }
        ),
    )

    def __init__(self, *args, hide_customer=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product_line"].queryset = _sim_line_queryset()
        self.fields["product_line"].label_from_instance = _sim_line_label
        if hide_customer:
            self.fields["customer"].widget = forms.HiddenInput(
                attrs={"id": "id_allocate_customer"}
            )


class ReturnFromCustomerForm(forms.Form):
    product_line = forms.ModelChoiceField(
        label=_("Product line"),
        queryset=ProductLine.objects.none(),
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    quantity = forms.IntegerField(
        label=_("Quantity"),
        min_value=1,
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm", "min": "1"}),
    )
    notes = forms.CharField(
        label=_("Notes"),
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control form-control-sm", "autocomplete": "off"}
        ),
    )
    serials = forms.CharField(
        label=_("Serial numbers (optional)"),
        required=False,
        help_text=_("One serial or ICCID per line; count must match quantity."),
        widget=forms.Textarea(
            attrs={
                "class": "form-control form-control-sm font-monospace rd-inv-serials-input",
                "rows": 1,
                "autocomplete": "off",
                "placeholder": _("One per line"),
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product_line"].queryset = _sim_line_queryset()
        self.fields["product_line"].label_from_instance = _sim_line_label


class SimCardSearchForm(forms.Form):
    q = forms.CharField(
        label=_("Serial or ICCID"),
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control form-control-sm", "placeholder": _("Search serial / ICCID…")}
        ),
    )


class SetBalanceQuantityForm(forms.Form):
    quantity = forms.IntegerField(
        label=_("New quantity"),
        min_value=0,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": "0"}),
    )
    reason = forms.CharField(
        label=_("Reason"),
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )


class ClearBalanceForm(forms.Form):
    reason = forms.CharField(
        label=_("Reason"),
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )


class AdjustBalanceForm(forms.Form):
    signed_delta = forms.IntegerField(
        label=_("Adjustment (+/-)"),
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    reason = forms.CharField(
        label=_("Reason"),
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )


class MarkDamagedForm(forms.Form):
    quantity = forms.IntegerField(
        label=_("Quantity"),
        min_value=1,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": "1"}),
    )
    notes = forms.CharField(
        label=_("Notes"),
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )


class MovementFilterForm(forms.Form):
    movement_type = forms.ChoiceField(
        label=_("Movement type"),
        required=False,
        choices=[("", _("All"))],
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    product_line = forms.ModelChoiceField(
        label=_("Product line"),
        queryset=ProductLine.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    customer = forms.ModelChoiceField(
        label=_("Customer"),
        queryset=Customer.objects.all().order_by("name"),
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    date_from = forms.DateField(
        label=_("Date from"),
        required=False,
        widget=forms.DateInput(attrs={"class": "form-control form-control-sm", "type": "date"}),
    )
    date_to = forms.DateField(
        label=_("Date to"),
        required=False,
        widget=forms.DateInput(attrs={"class": "form-control form-control-sm", "type": "date"}),
    )

    def __init__(self, *args, **kwargs):
        from inventory.models import SimStockMovement

        super().__init__(*args, **kwargs)
        self.fields["movement_type"].choices = [("", _("All"))] + list(
            SimStockMovement.MovementType.choices
        )
        self.fields["product_line"].queryset = _sim_line_queryset()
        self.fields["product_line"].label_from_instance = _sim_line_label


class CustomerStockFilterForm(forms.Form):
    q = forms.CharField(
        label=_("Search"),
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-sm",
                "id": "id_inv_customer_q",
                "autocomplete": "off",
                "placeholder": _("Customer name…"),
            }
        ),
    )
    product_line = forms.ModelChoiceField(
        label=_("Product line"),
        queryset=ProductLine.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product_line"].queryset = _sim_line_queryset()
        self.fields["product_line"].label_from_instance = _sim_line_label
