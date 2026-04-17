from django import forms
from django.utils.translation import gettext_lazy as _

from expenses.models import Expense


class ExpenseListFilterForm(forms.Form):
    q = forms.CharField(
        required=False,
        label=_("Search"),
        widget=forms.TextInput(
            attrs={"class": "form-control form-control-sm", "placeholder": _("Title, category, notes…")}
        ),
    )
    date_from = forms.DateField(
        required=False,
        label=_("Date from"),
        widget=forms.DateInput(attrs={"class": "form-control form-control-sm", "type": "date"}),
    )
    date_to = forms.DateField(
        required=False,
        label=_("Date to"),
        widget=forms.DateInput(attrs={"class": "form-control form-control-sm", "type": "date"}),
    )
    category = forms.CharField(
        required=False,
        label=_("Category"),
        widget=forms.TextInput(attrs={"class": "form-control form-control-sm"}),
    )


class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = ["title", "category", "amount", "date", "notes"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "category": forms.TextInput(attrs={"class": "form-control"}),
            "amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        }
