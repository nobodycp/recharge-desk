from django.contrib import messages
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from accounts.permissions import management_required
from core.pagination import paginate_request
from expenses.forms import ExpenseForm, ExpenseListFilterForm
from expenses.models import Expense
from expenses.query_utils import apply_expense_list_ordering
from sales.models import Sale
from sales.query_utils import paid_sales_only


@management_required
def expense_list(request):
    qs = Expense.objects.select_related("created_by")
    form = ExpenseListFilterForm(request.GET or None)
    d = form.cleaned_data if form.is_valid() else {}
    if d.get("q"):
        qs = qs.filter(
            Q(title__icontains=d["q"])
            | Q(category__icontains=d["q"])
            | Q(notes__icontains=d["q"])
        )
    if d.get("category"):
        qs = qs.filter(category__icontains=d["category"])
    if d.get("date_from"):
        qs = qs.filter(date__gte=d["date_from"])
    if d.get("date_to"):
        qs = qs.filter(date__lte=d["date_to"])
    qs = apply_expense_list_ordering(request, qs)
    page_obj = paginate_request(request, qs)
    ctx = {
        "page_obj": page_obj,
        "filter_form": form,
        "title": _("Expenses"),
        "sort": request.GET.get("sort") or "date",
        "order": (request.GET.get("order") or "desc").lower(),
    }
    if request.headers.get("HX-Request"):
        return render(request, "expenses/partials/expense_list_results.html", ctx)
    return render(request, "expenses/expense_list.html", ctx)


@management_required
def expense_create(request):
    form = ExpenseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.created_by = request.user
        obj.save()
        messages.success(request, _("Expense saved."))
        return redirect("expenses:expense_list")
    return render(
        request,
        "expenses/expense_form.html",
        {"form": form, "title": _("New expense")},
    )


@management_required
def expense_edit(request, pk):
    expense = get_object_or_404(Expense, pk=pk)
    form = ExpenseForm(request.POST or None, instance=expense)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Expense updated."))
        return redirect("expenses:expense_list")
    return render(
        request,
        "expenses/expense_form.html",
        {"form": form, "title": _("Edit expense")},
    )


def _expense_delete_redirect(request):
    nxt = (request.POST.get("next") or "").strip()
    if nxt.startswith("/") and not nxt.startswith("//"):
        return redirect(nxt)
    return redirect("expenses:expense_list")


@management_required
@require_POST
def expense_delete(request, pk):
    expense = get_object_or_404(Expense, pk=pk)
    title = expense.title
    expense.delete()
    messages.success(request, _("Expense \"%(title)s\" deleted.") % {"title": title})
    return _expense_delete_redirect(request)


@management_required
def expense_report(request):
    from django import forms

    class F(forms.Form):
        date_from = forms.DateField(
            required=False,
            label=_("Date from"),
            widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
        )
        date_to = forms.DateField(
            required=False,
            label=_("Date to"),
            widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
        )

    form = F(request.GET or None)
    qs = Expense.objects.all()
    if form.is_valid():
        if form.cleaned_data.get("date_from"):
            qs = qs.filter(date__gte=form.cleaned_data["date_from"])
        if form.cleaned_data.get("date_to"):
            qs = qs.filter(date__lte=form.cleaned_data["date_to"])
    total = qs.aggregate(t=Sum("amount"))["t"] or 0
    by_cat = {}
    for row in qs.values("category").annotate(s=Sum("amount")).order_by():
        by_cat[row["category"]] = row["s"]
    by_category_rows = sorted(by_cat.items(), key=lambda x: x[0])

    profit_qs = paid_sales_only(Sale.objects.all())
    if form.is_valid():
        if form.cleaned_data.get("date_from"):
            profit_qs = profit_qs.filter(created_at__date__gte=form.cleaned_data["date_from"])
        if form.cleaned_data.get("date_to"):
            profit_qs = profit_qs.filter(created_at__date__lte=form.cleaned_data["date_to"])
    profit_total = profit_qs.aggregate(s=Sum("profit_snapshot"))["s"] or 0
    net_profit = profit_total - total

    return render(
        request,
        "expenses/expense_report.html",
        {
            "form": form,
            "expenses": qs.order_by("-date"),
            "total": total,
            "by_category_rows": by_category_rows,
            "profit_total": profit_total,
            "net_profit": net_profit,
            "title": _("Expense report"),
        },
    )
