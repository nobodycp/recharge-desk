from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _

from companies.models import Company, Product, ProductLine


class CompanyForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = ["name", "icon", "opening_balance", "phone_refresh_provider", "notes", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "icon": forms.ClearableFileInput(attrs={"class": "form-control"}),
            "opening_balance": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "phone_refresh_provider": forms.Select(attrs={"class": "form-select"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def save(self, commit=True):
        instance = super().save(commit=False)
        if instance.pk is None:
            instance.current_balance = Decimal("0")
        if commit:
            instance.save()
        return instance


class ProductLineForm(forms.ModelForm):
    class Meta:
        model = ProductLine
        fields = [
            "company",
            "name",
            "icon",
            "sort_order",
            "estimated_unit_cost",
            "is_active",
            "default_package",
        ]
        widgets = {
            "company": forms.Select(attrs={"class": "form-select"}),
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "icon": forms.ClearableFileInput(attrs={"class": "form-control"}),
            "sort_order": forms.NumberInput(attrs={"class": "form-control", "min": "0"}),
            "estimated_unit_cost": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.01", "min": "0"}
            ),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "default_package": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            del self.fields["default_package"]
        else:
            self.fields["default_package"].queryset = Product.objects.filter(line=self.instance).order_by(
                "variant_label"
            )
            self.fields["default_package"].required = False

    def clean(self):
        cleaned = super().clean()
        dp = cleaned.get("default_package")
        if dp and self.instance.pk and dp.line_id != self.instance.pk:
            self.add_error(
                "default_package",
                _("Select a package that belongs to this product line."),
            )
        return cleaned


class LayanReportReconcileForm(forms.Form):
    period_from = forms.DateField(
        label=_("Date from"),
        required=False,
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )
    period_to = forms.DateField(
        label=_("Date to"),
        required=False,
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )
    report_file = forms.FileField(
        label=_("Layan charges report (.xlsx)"),
        widget=forms.ClearableFileInput(attrs={"class": "form-control", "accept": ".xlsx,.xls"}),
    )
    pending_credits = forms.CharField(
        label=_("Pending portal credits (optional)"),
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "512823671,29",
            }
        ),
        help_text=_(
            "One line per entry: phone,credit amount not yet shown on the export "
            "(e.g. pending reversal)."
        ),
    )
    min_settlement_difference = forms.DecimalField(
        label=_("Minimum difference to show"),
        required=False,
        initial=3,
        min_value=0,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
        help_text=_(
            "Used for settlements (charge + disconnect retained amount) and for "
            "sales differences between Layan and the system. Rows with a smaller "
            "difference are hidden (0 shows all)."
        ),
    )


class ProductVariantForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ["variant_label", "icon", "cost_price", "default_sell_price", "is_active"]
        widgets = {
            "variant_label": forms.TextInput(attrs={"class": "form-control", "placeholder": _("e.g. 100 GB")}),
            "icon": forms.ClearableFileInput(attrs={"class": "form-control"}),
            "cost_price": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "default_sell_price": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
