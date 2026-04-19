from django import forms
from django.contrib import messages
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate, TruncMonth
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.permissions import management_required
from core.pagination import paginate_request
from sales.ledger_query_utils import apply_ledger_list_ordering, filter_ledger_queryset
from sales.query_utils import (
    EXCLUDED_AGGREGATE_STATUSES,
    apply_management_sale_filter_data,
    apply_sale_list_ordering,
    confirmed_sales,
    paid_sales_only,
)
from companies.models import Company
from expenses.models import Expense
from sales.forms import BalanceAdjustmentForm, ManualDepositForm
from sales.models import CompanyBalanceTransaction, Sale
from sales.services import record_balance_adjustment, record_manual_deposit


def _local_today():
    return timezone.localdate()


@management_required
def dashboard(request):
    today = _local_today()
    from customers.models import Customer

    sales_base = confirmed_sales(Sale.objects.all())
    pending_count = Sale.objects.filter(status=Sale.Status.PENDING).count()
    awaiting_count = Sale.objects.filter(status=Sale.Status.AWAITING).count()
    customer_debt_total = (
        Customer.objects.filter(current_balance__gt=0)
        .aggregate(s=Sum("current_balance"))["s"]
        or 0
    )
    today_sales = sales_base.filter(created_at__date=today)
    today_count = today_sales.count()
    today_volume = today_sales.aggregate(s=Sum("sell_price_actual"))["s"] or 0
    today_profit = (
        paid_sales_only(today_sales).aggregate(s=Sum("profit_snapshot"))["s"] or 0
    )

    total_profit = (
        paid_sales_only(Sale.objects.all()).aggregate(s=Sum("profit_snapshot"))["s"] or 0
    )
    total_expenses = Expense.objects.aggregate(s=Sum("amount"))["s"] or 0
    net_all_time = (total_profit or 0) - (total_expenses or 0)

    month_start = today.replace(day=1)
    month_sales = sales_base.filter(created_at__date__gte=month_start)
    month_profit = (
        paid_sales_only(month_sales).aggregate(s=Sum("profit_snapshot"))["s"] or 0
    )
    month_expenses = Expense.objects.filter(date__gte=month_start).aggregate(s=Sum("amount"))["s"] or 0
    net_month = (month_profit or 0) - (month_expenses or 0)

    companies = Company.objects.filter(is_active=True).order_by("name")
    recent_sales = (
        Sale.objects.select_related("company", "product", "product__line", "created_by", "payment_method")
        .order_by("-created_at")[:12]
    )
    esim_sales_count = confirmed_sales(Sale.objects.filter(is_esim=True)).count()
    today_loss_from_zero = today_sales.aggregate(s=Sum("loss_snapshot"))["s"] or 0
    month_loss_from_zero = month_sales.aggregate(s=Sum("loss_snapshot"))["s"] or 0
    total_loss_from_zero = sales_base.aggregate(s=Sum("loss_snapshot"))["s"] or 0
    return render(
        request,
        "reports/dashboard.html",
        {
            "title": _("Dashboard"),
            "pending_count": pending_count,
            "awaiting_count": awaiting_count,
            "customer_debt_total": customer_debt_total,
            "esim_sales_count": esim_sales_count,
            "today_loss_from_zero": today_loss_from_zero,
            "month_loss_from_zero": month_loss_from_zero,
            "total_loss_from_zero": total_loss_from_zero,
            "today_count": today_count,
            "today_volume": today_volume,
            "today_profit": today_profit,
            "total_profit": total_profit,
            "total_expenses": total_expenses,
            "net_all_time": net_all_time,
            "month_profit": month_profit,
            "month_expenses": month_expenses or 0,
            "net_month": net_month,
            "companies": companies,
            "recent_sales": recent_sales,
        },
    )


class DateRangeForm(forms.Form):
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


@management_required
def profit_report(request):
    form = DateRangeForm(request.GET or None)
    qs = confirmed_sales(Sale.objects.all())
    if form.is_valid():
        if form.cleaned_data.get("date_from"):
            qs = qs.filter(created_at__date__gte=form.cleaned_data["date_from"])
        if form.cleaned_data.get("date_to"):
            qs = qs.filter(created_at__date__lte=form.cleaned_data["date_to"])
    qs_paid = paid_sales_only(qs)
    total_profit = qs_paid.aggregate(s=Sum("profit_snapshot"))["s"] or 0
    by_company = (
        qs_paid.values("company__name")
        .annotate(profit=Sum("profit_snapshot"), volume=Sum("sell_price_actual"), cnt=Count("id"))
        .order_by("-profit")
    )
    by_product = (
        qs_paid.values(
            "company__name",
            "product__line__name",
            "product__variant_label",
        )
        .annotate(profit=Sum("profit_snapshot"), cnt=Count("id"))
        .order_by("-profit")[:40]
    )
    by_employee = (
        qs_paid.values("created_by__username")
        .annotate(profit=Sum("profit_snapshot"), cnt=Count("id"))
        .order_by("-profit")
    )
    by_method = (
        qs_paid.values("payment_method__name")
        .annotate(profit=Sum("profit_snapshot"), cnt=Count("id"))
        .order_by("-profit")
    )
    daily = (
        qs_paid.annotate(d=TruncDate("created_at"))
        .values("d")
        .annotate(profit=Sum("profit_snapshot"))
        .order_by("d")
    )
    monthly = (
        qs_paid.annotate(m=TruncMonth("created_at"))
        .values("m")
        .annotate(profit=Sum("profit_snapshot"))
        .order_by("m")
    )
    return render(
        request,
        "reports/profit_report.html",
        {
            "form": form,
            "title": _("Profit report"),
            "total_profit": total_profit,
            "by_company": by_company,
            "by_product": by_product,
            "by_employee": by_employee,
            "by_method": by_method,
            "daily": daily,
            "monthly": monthly,
        },
    )


@management_required
def sales_report(request):
    """Alias filters reusing sales list logic via redirect or duplicate — embed quick summary."""
    from sales.forms import ManagementSaleFilterForm

    form = ManagementSaleFilterForm(request.GET or None)
    qs = Sale.objects.select_related(
        "company", "product", "product__line", "payment_method", "created_by"
    ).order_by("-created_at")
    data = form.cleaned_data if form.is_valid() else {}
    qs = apply_management_sale_filter_data(qs, data)
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(reference_number__icontains=q)
            | Q(payer_name__icontains=q)
            | Q(product__line__name__icontains=q)
            | Q(company__name__icontains=q)
        )
    non_cancelled = confirmed_sales(qs)
    summary = {
        **non_cancelled.aggregate(volume=Sum("sell_price_actual"), cnt=Count("id")),
        **paid_sales_only(qs).aggregate(profit=Sum("profit_snapshot")),
    }
    qs = apply_sale_list_ordering(request, qs)
    page_obj = paginate_request(request, qs)
    ctx = {
        "form": form,
        "page_obj": page_obj,
        "summary": summary,
        "title": _("Sales report"),
        "sort": request.GET.get("sort") or "created_at",
        "order": (request.GET.get("order") or "desc").lower(),
    }
    if request.headers.get("HX-Request"):
        return render(request, "reports/partials/sales_report_results.html", ctx)
    return render(request, "reports/sales_report.html", ctx)


def _company_report_sales_queryset(company, request):
    qs = company.sales.select_related("product", "product__line", "payment_method", "created_by")
    sales_q = (request.GET.get("sales_q") or "").strip()
    if sales_q:
        qs = qs.filter(
            Q(reference_number__icontains=sales_q)
            | Q(payer_name__icontains=sales_q)
            | Q(product__line__name__icontains=sales_q)
            | Q(product__variant_label__icontains=sales_q)
        )
    return apply_sale_list_ordering(
        request, qs, sort_param="sales_sort", order_param="sales_order"
    )


def _company_report_ledger_queryset(company, request):
    qs = company.balance_transactions.select_related("created_by")
    qs = filter_ledger_queryset(qs, request.GET.get("ledger_q") or "")
    return apply_ledger_list_ordering(request, qs)


@management_required
def company_report(request, pk):
    company = get_object_or_404(Company, pk=pk)
    deposit_form = ManualDepositForm(request.POST or None, prefix="dep")
    adj_form = BalanceAdjustmentForm(request.POST or None, prefix="adj")

    if request.method == "POST":
        if "dep-submit" in request.POST and deposit_form.is_valid():
            record_manual_deposit(
                company=company,
                amount=deposit_form.cleaned_data["amount"],
                notes=deposit_form.cleaned_data.get("notes") or "",
                user=request.user,
            )
            messages.success(request, _("Deposit recorded."))
            return redirect("reports:company_report", pk=company.pk)
        if "adj-submit" in request.POST and adj_form.is_valid():
            record_balance_adjustment(
                company=company,
                signed_amount=adj_form.cleaned_data["signed_amount"],
                notes=adj_form.cleaned_data.get("notes") or "",
                user=request.user,
            )
            messages.success(request, _("Adjustment recorded."))
            return redirect("reports:company_report", pk=company.pk)

    ledger_qs_all = company.balance_transactions.all()
    sales_qs_filtered = _company_report_sales_queryset(company, request)
    ledger_qs_filtered = _company_report_ledger_queryset(company, request)

    sales_page = paginate_request(request, sales_qs_filtered, page_param="sales_page")
    ledger_page = paginate_request(request, ledger_qs_filtered, page_param="ledger_page")

    deposits = ledger_qs_all.filter(entry_type=CompanyBalanceTransaction.EntryType.DEPOSIT).aggregate(
        s=Sum("amount")
    )["s"] or 0
    consumed = ledger_qs_all.filter(entry_type=CompanyBalanceTransaction.EntryType.DEDUCTION).aggregate(
        s=Sum("amount")
    )["s"] or 0
    reversals = ledger_qs_all.filter(entry_type=CompanyBalanceTransaction.EntryType.REVERSAL).aggregate(
        s=Sum("amount")
    )["s"] or 0
    adjustments = ledger_qs_all.filter(entry_type=CompanyBalanceTransaction.EntryType.ADJUSTMENT).aggregate(
        s=Sum("amount")
    )["s"] or 0

    sales_non_cancelled = confirmed_sales(sales_qs_filtered)
    agg = {
        **sales_non_cancelled.aggregate(cnt=Count("id"), total_sell=Sum("sell_price_actual")),
        **paid_sales_only(sales_qs_filtered).aggregate(total_profit=Sum("profit_snapshot")),
    }

    ctx = {
        "company": company,
        "ledger_page": ledger_page,
        "sales_page": sales_page,
        "deposits_total": deposits,
        "consumed_total": consumed,
        "reversals_total": reversals or 0,
        "adjustments_total": adjustments or 0,
        "agg": agg,
        "deposit_form": deposit_form,
        "adj_form": adj_form,
        "title": _("Company report"),
        "sales_sort": request.GET.get("sales_sort") or "created_at",
        "sales_order": (request.GET.get("sales_order") or "desc").lower(),
        "ledger_sort": request.GET.get("ledger_sort") or "created_at",
        "ledger_order": (request.GET.get("ledger_order") or "desc").lower(),
    }
    if request.headers.get("HX-Request"):
        frag = (request.GET.get("partial") or "").strip()
        if frag == "sales":
            return render(request, "reports/partials/company_report_sales.html", ctx)
        if frag == "ledger":
            return render(request, "reports/partials/company_report_ledger.html", ctx)
    return render(request, "reports/company_report.html", ctx)
