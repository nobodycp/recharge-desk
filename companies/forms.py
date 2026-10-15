from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _

from companies.models import Company, Product, ProductLine


class CompanySearchForm(forms.Form):
    """Render-only filter form for the company list (the view reads ``request.GET``)."""

    q = forms.CharField(
        required=False,
        label=_("Search"),
        widget=forms.TextInput(attrs={"placeholder": _("Company name or notes…")}),
    )


class CompanyForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = ["name", "icon", "opening_balance", "provider_key", "notes", "is_active"]
        widgets = {
            "opening_balance": forms.NumberInput(attrs={"step": "0.01"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
            "is_active": forms.CheckboxInput(attrs={"role": "switch"}),
        }

    def save(self, commit=True):
        instance = super().save(commit=False)
        if instance.pk is None:
            instance.current_balance = Decimal("0")
        if commit:
            instance.save()
        return instance


class ProductSearchForm(forms.Form):
    """Render-only filter form for the product list (the view reads ``request.GET``)."""

    q = forms.CharField(
        required=False,
        label=_("Search"),
        widget=forms.TextInput(
            attrs={"type": "search", "placeholder": _("Company, line, or package…"), "autocomplete": "off"}
        ),
    )
    company = forms.ChoiceField(required=False, label=_("Company"))
    show_archived = forms.BooleanField(required=False, label=_("Show archived packages"))

    def __init__(self, *args, companies=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["company"].choices = [
            ("", _("All companies")),
            *((str(c.pk), c.name) for c in companies),
        ]


class ProductLineForm(forms.ModelForm):
    class Meta:
        model = ProductLine
        fields = [
            "company",
            "name",
            "icon",
            "sort_order",
            "estimated_unit_cost",
            "allow_partial_days",
            "is_active",
            "default_package",
        ]
        widgets = {
            "sort_order": forms.NumberInput(attrs={"min": "0"}),
            "estimated_unit_cost": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "allow_partial_days": forms.CheckboxInput(attrs={"role": "switch"}),
            "is_active": forms.CheckboxInput(attrs={"role": "switch"}),
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


class PeriodFilterForm(forms.Form):
    """Render-only filter form for the company statement period (the
    ``period_from``/``period_to`` GET params are still parsed by the view;
    see ``companies.statement.parse_company_statement_period``).

    Widget ids are pinned to ``cr-period-from`` / ``cr-period-to`` since
    they are referenced by existing tests/automation.
    """

    period_from = forms.DateField(
        label=_("Date from"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "id": "cr-period-from"}),
    )
    period_to = forms.DateField(
        label=_("Date to"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "id": "cr-period-to"}),
    )


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


class SkyReportReconcileForm(forms.Form):
    period_from = forms.DateField(
        label=_("Date from"),
        required=True,
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )
    period_to = forms.DateField(
        label=_("Date to"),
        required=True,
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )
    min_amount_diff = forms.DecimalField(
        label=_("Minimum difference to show"),
        required=False,
        initial=3,
        min_value=0,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
        help_text=_("Below this amount counts as matched. Use 0 to show all."),
    )

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("period_from")
        end = cleaned.get("period_to")
        if start and end and end < start:
            self.add_error("period_to", _("Date to must be on or after date from."))
        return cleaned


class ProductVariantForm(forms.ModelForm):
    base_days = forms.TypedChoiceField(
        label=_("Package period"),
        coerce=int,
        choices=(
            (30, _("Monthly (30 days) — default")),
            (90, _("3 months (90 days)")),
        ),
        initial=30,
        help_text=_("Leave as monthly unless this package is a 3-month offer."),
    )

    class Meta:
        model = Product
        # Package icons are not used — the product-line icon is shared by all packages.
        fields = ["variant_label", "cost_price", "default_sell_price", "base_days", "is_active"]
        widgets = {
            "variant_label": forms.TextInput(attrs={"placeholder": _("e.g. 100 GB")}),
            "cost_price": forms.NumberInput(attrs={"step": "0.01"}),
            "default_sell_price": forms.NumberInput(attrs={"step": "0.01"}),
            "is_active": forms.CheckboxInput(attrs={"role": "switch"}),
        }
