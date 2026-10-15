from django import forms
from django.utils.translation import gettext_lazy as _

from expenses.models import Expense


class ExpenseListFilterForm(forms.Form):
    q = forms.CharField(
        required=False,
        label=_("Search"),
        widget=forms.TextInput(attrs={"placeholder": _("Title, category, notes…")}),
    )
    date_from = forms.DateField(
        required=False,
        label=_("Date from"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    date_to = forms.DateField(
        required=False,
        label=_("Date to"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    category = forms.CharField(
        required=False,
        label=_("Category"),
        widget=forms.TextInput(),
    )


class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = ["title", "category", "amount", "date", "notes"]
        widgets = {
            "amount": forms.NumberInput(attrs={"step": "0.01"}),
            "date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }


class ExpenseReportFilterForm(forms.Form):
    date_from = forms.DateField(
        required=False,
        label=_("Date from"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    date_to = forms.DateField(
        required=False,
        label=_("Date to"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
