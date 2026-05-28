from django import forms
from django.contrib import messages
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate, TruncMonth
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.permissions import management_required
from core.kpi_cache import cached_kpi
from core.pagination import paginate_request
from sales.ledger_query_utils import apply_ledger_list_ordering, filter_ledger_queryset
from sales.query_utils import (
    EXCLUDED_AGGREGATE_STATUSES,
    apply_management_sale_filter_data,
    apply_sale_list_ordering,
    confirmed_sales,
    loss_eligible_sales,
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
    from customers.models import Customer, CustomerPaymentSubmission

    # Each scalar aggregate below is wrapped in `cached_kpi`. The cache
    # version is bumped automatically by core.signals whenever a Sale,
    # Expense, CompanyBalanceTransaction, CustomerLedger, CustomerPayment,
    # or CustomerPaymentSubmission row changes — see core.kpi_cache for the full
    # rationale. Date-bucketed values include `today` in the cache key
    # so a midnight rollover doesn't serve yesterday's number.
    today_key = today.isoformat()
    month_start = today.replace(day=1)
    month_key = month_start.isoformat()

    pending_count = cached_kpi(
        "dashboard:pending_count",
        lambda: Sale.objects.filter(
            status=Sale.Status.PENDING, on_account=False
        ).count(),
    )
    awaiting_count = cached_kpi(
        "dashboard:awaiting_count",
        lambda: Sale.objects.filter(status=Sale.Status.AWAITING).count(),
    )
    payment_submissions_awaiting_count = cached_kpi(
        "dashboard:payment_submissions_awaiting_count",
        lambda: CustomerPaymentSubmission.objects.filter(
            status=CustomerPaymentSubmission.Status.AWAITING
        ).count(),
    )
    customer_debt_total = cached_kpi(
        "dashboard:customer_debt_total",
        lambda: Customer.objects.filter(current_balance__gt=0).aggregate(
            s=Sum("current_balance")
        )["s"]
        or 0,
    )

    def _today_sales():
        return confirmed_sales(Sale.objects.all()).filter(created_at__date=today)

    today_count = cached_kpi(
        f"dashboard:today_count:{today_key}",
        lambda: _today_sales().count(),
    )
    today_volume = cached_kpi(
        f"dashboard:today_volume:{today_key}",
        lambda: _today_sales().aggregate(s=Sum("sell_price_actual"))["s"] or 0,
    )
    today_profit = cached_kpi(
        f"dashboard:today_profit:{today_key}",
        lambda: paid_sales_only(_today_sales()).aggregate(s=Sum("profit_snapshot"))["s"]
        or 0,
    )

    total_profit = cached_kpi(
        "dashboard:total_profit",
        lambda: paid_sales_only(Sale.objects.all()).aggregate(
            s=Sum("profit_snapshot")
        )["s"]
        or 0,
    )
    total_expenses = cached_kpi(
        "dashboard:total_expenses",
        lambda: Expense.objects.aggregate(s=Sum("amount"))["s"] or 0,
    )
    net_all_time = (total_profit or 0) - (total_expenses or 0)

    def _month_sales():
        return confirmed_sales(Sale.objects.all()).filter(created_at__date__gte=month_start)

    month_profit = cached_kpi(
        f"dashboard:month_profit:{month_key}",
        lambda: paid_sales_only(_month_sales()).aggregate(s=Sum("profit_snapshot"))["s"]
        or 0,
    )
    month_expenses = cached_kpi(
        f"dashboard:month_expenses:{month_key}",
        lambda: Expense.objects.filter(date__gte=month_start).aggregate(s=Sum("amount"))["s"]
        or 0,
    )
    net_month = (month_profit or 0) - (month_expenses or 0)

    # `companies` and `recent_sales` are short queries that drive the
    # left/right cards and the activity table. They're not cached
    # because they return queryset rows (not scalars) and we want fresh
    # ordering and live-icon resolution.
    companies = Company.objects.filter(is_active=True).order_by("name")
    recent_sales = (
        Sale.objects.select_related(
            "company",
            "product",
            "product__line",
            "created_by",
            "payment_method",
            "employee_recipient",
            "employee_recipient__user",
            "employee_recipient__user__profile",
        )
        .order_by("-created_at")[:12]
    )

    # Must use the same date scope as `today_count` / `today_volume` (today only).
    esim_sales_count = cached_kpi(
        f"dashboard:esim_sales_count:{today_key}",
        lambda: _today_sales().filter(is_esim=True).count(),
    )
    all_sales = Sale.objects.all()
    today_loss_from_zero = cached_kpi(
        f"dashboard:today_loss:{today_key}",
        lambda: loss_eligible_sales(all_sales.filter(created_at__date=today)).aggregate(
            s=Sum("loss_snapshot")
        )["s"]
        or 0,
    )
    month_loss_from_zero = cached_kpi(
        f"dashboard:month_loss:{month_key}",
        lambda: loss_eligible_sales(
            all_sales.filter(created_at__date__gte=month_start)
        ).aggregate(s=Sum("loss_snapshot"))["s"]
        or 0,
    )
    total_loss_from_zero = cached_kpi(
        "dashboard:total_loss",
        lambda: loss_eligible_sales(all_sales).aggregate(s=Sum("loss_snapshot"))["s"]
        or 0,
    )
    # 14-day sales sparkline + top-5 companies bar chart for the new
    # mini-charts row on the dashboard. Both queries are cheap (a single
    # GROUP BY on the indexed created_at) and cached for the day so a
    # busy dashboard doesn't hit them on every refresh.
    chart_window_days = 14

    def _daily_series():
        from datetime import timedelta

        start = today - timedelta(days=chart_window_days - 1)
        rows = (
            confirmed_sales(Sale.objects.all())
            .filter(created_at__date__gte=start)
            .annotate(d=TruncDate("created_at"))
            .values("d")
            .annotate(volume=Sum("sell_price_actual"), cnt=Count("id"))
            .order_by("d")
        )
        # One-letter weekday labels (Sat→س … Fri→ج), common in Arabic UIs.
        _ar_day_letter = {0: "ن", 1: "ث", 2: "ر", 3: "خ", 4: "ج", 5: "س", 6: "ح"}

        bucket = {r["d"]: (float(r["volume"] or 0), int(r["cnt"] or 0)) for r in rows}
        out = []
        for i in range(chart_window_days):
            day = start + timedelta(days=i)
            v, c = bucket.get(day, (0.0, 0))
            out.append(
                {
                    "date": day.isoformat(),
                    "weekday": day,
                    "day_letter": _ar_day_letter[day.weekday()],
                    "volume": v,
                    "count": c,
                }
            )
        return out

    def _top_companies():
        rows = (
            paid_sales_only(_month_sales())
            .values("company__name")
            .annotate(profit=Sum("profit_snapshot"), volume=Sum("sell_price_actual"))
            .order_by("-profit")[:5]
        )
        return [
            {
                "name": r["company__name"] or "—",
                "profit": float(r["profit"] or 0),
                "volume": float(r["volume"] or 0),
            }
            for r in rows
        ]

    daily_series = cached_kpi(
        f"dashboard:daily_series_v3:{today_key}", _daily_series
    )
    top_companies = cached_kpi(
        f"dashboard:top_companies:{month_key}", _top_companies
    )

    chart_max_volume = max((d["volume"] for d in daily_series), default=0) or 1
    chart_max_company_profit = max((c["profit"] for c in top_companies), default=0) or 1
    chart_total_volume = sum(d["volume"] for d in daily_series)
    chart_avg_volume = (
        chart_total_volume / chart_window_days if chart_window_days else 0
    )

    return render(
        request,
        "reports/dashboard.html",
        {
            "title": _("Dashboard"),
            "pending_count": pending_count,
            "awaiting_count": awaiting_count,
            "payment_submissions_awaiting_count": payment_submissions_awaiting_count,
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
            "daily_series": daily_series,
            "top_companies": top_companies,
            "chart_max_volume": chart_max_volume,
            "chart_max_company_profit": chart_max_company_profit,
            "chart_window_days": chart_window_days,
            "chart_total_volume": chart_total_volume,
            "chart_avg_volume": chart_avg_volume,
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
def employee_report(request):
    """Per-employee KPIs (sales count, volume, profit) within a date range.

    The default window is the current month so the page always lands on
    actionable numbers without forcing the user to fill the form first.
    The "best company" column does a second pass per employee — N is
    bounded by the number of staff so this stays cheap.
    """
    today = _local_today()
    default_from = today.replace(day=1)

    form = DateRangeForm(request.GET or None)
    date_from = default_from
    date_to = today
    if form.is_valid():
        date_from = form.cleaned_data.get("date_from") or default_from
        date_to = form.cleaned_data.get("date_to") or today

    qs = confirmed_sales(Sale.objects.all()).filter(
        created_at__date__gte=date_from,
        created_at__date__lte=date_to,
    )
    qs_paid = paid_sales_only(qs)

    summary = {
        "sales_count": qs.count(),
        "volume": qs.aggregate(s=Sum("sell_price_actual"))["s"] or 0,
        "profit": qs_paid.aggregate(s=Sum("profit_snapshot"))["s"] or 0,
        "active_staff": qs.values("created_by_id").distinct().count(),
    }

    by_employee = list(
        qs.values("created_by_id", "created_by__username", "created_by__first_name")
        .annotate(
            sales_count=Count("id"),
            volume=Sum("sell_price_actual"),
        )
        .order_by("-volume")
    )

    profit_by_employee = {
        row["created_by_id"]: row["profit"] or 0
        for row in qs_paid.values("created_by_id").annotate(profit=Sum("profit_snapshot"))
    }

    top_company_by_employee = {}
    for row in (
        qs.values("created_by_id", "company__name")
        .annotate(volume=Sum("sell_price_actual"))
        .order_by("created_by_id", "-volume")
    ):
        emp_id = row["created_by_id"]
        if emp_id in top_company_by_employee:
            continue
        top_company_by_employee[emp_id] = row["company__name"]

    rows = []
    for r in by_employee:
        emp_id = r["created_by_id"]
        sales_count = r["sales_count"] or 0
        volume = r["volume"] or 0
        profit = profit_by_employee.get(emp_id, 0)
        rows.append(
            {
                "employee_id": emp_id,
                "username": r["created_by__username"] or _("Unknown"),
                "first_name": r["created_by__first_name"] or "",
                "sales_count": sales_count,
                "volume": volume,
                "profit": profit,
                "avg_ticket": (volume / sales_count) if sales_count else 0,
                "top_company": top_company_by_employee.get(emp_id) or "—",
            }
        )

    return render(
        request,
        "reports/employee_report.html",
        {
            "title": _("Employee performance"),
            "form": form,
            "date_from": date_from,
            "date_to": date_to,
            "summary": summary,
            "rows": rows,
        },
    )


@management_required
def sales_report(request):
    """Alias filters reusing sales list logic via redirect or duplicate — embed quick summary."""
    from sales.forms import ManagementSaleFilterForm

    form = ManagementSaleFilterForm(request.GET or None)
    qs = Sale.objects.select_related(
        "company",
        "product",
        "product__line",
        "payment_method",
        "created_by",
        "employee_recipient",
        "employee_recipient__user",
        "employee_recipient__user__profile",
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


def _parse_company_report_period(request):
    """Read ``period_from`` / ``period_to`` GET params into ``date`` objects.

    Returns ``(date_from, date_to)``; either side may be ``None`` when the
    user omits or supplies an invalid value, in which case that bound is
    treated as open-ended ("all time").
    """
    from datetime import date as _date

    def _parse(s):
        try:
            return _date.fromisoformat((s or "").strip())
        except (ValueError, TypeError):
            return None

    return _parse(request.GET.get("period_from")), _parse(request.GET.get("period_to"))


def _apply_period(qs, date_from, date_to, field="created_at"):
    """Inclusive ``date_from``/``date_to`` filter on a datetime field."""
    if date_from:
        qs = qs.filter(**{f"{field}__date__gte": date_from})
    if date_to:
        qs = qs.filter(**{f"{field}__date__lte": date_to})
    return qs


def _company_report_sales_queryset(company, request, *, date_from=None, date_to=None):
    qs = company.sales.select_related(
        "product",
        "product__line",
        "payment_method",
        "created_by",
        "employee_recipient",
        "employee_recipient__user",
        "employee_recipient__user__profile",
    )
    sales_q = (request.GET.get("sales_q") or "").strip()
    if sales_q:
        qs = qs.filter(
            Q(reference_number__icontains=sales_q)
            | Q(payer_name__icontains=sales_q)
            | Q(product__line__name__icontains=sales_q)
            | Q(product__variant_label__icontains=sales_q)
        )
    qs = _apply_period(qs, date_from, date_to)
    return apply_sale_list_ordering(
        request, qs, sort_param="sales_sort", order_param="sales_order"
    )


def _company_report_ledger_queryset(company, request, *, date_from=None, date_to=None):
    qs = company.balance_transactions.select_related("created_by")
    qs = filter_ledger_queryset(qs, request.GET.get("ledger_q") or "")
    qs = _apply_period(qs, date_from, date_to)
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

    period_from, period_to = _parse_company_report_period(request)
    period_active = bool(period_from or period_to)

    ledger_qs_all = company.balance_transactions.all()
    ledger_qs_period = _apply_period(ledger_qs_all, period_from, period_to)
    sales_qs_filtered = _company_report_sales_queryset(
        company, request, date_from=period_from, date_to=period_to
    )
    ledger_qs_filtered = _company_report_ledger_queryset(
        company, request, date_from=period_from, date_to=period_to
    )

    sales_page = paginate_request(request, sales_qs_filtered, page_param="sales_page")
    ledger_page = paginate_request(request, ledger_qs_filtered, page_param="ledger_page")

    deposits = ledger_qs_period.filter(entry_type=CompanyBalanceTransaction.EntryType.DEPOSIT).aggregate(
        s=Sum("amount")
    )["s"] or 0
    consumed = ledger_qs_period.filter(entry_type=CompanyBalanceTransaction.EntryType.DEDUCTION).aggregate(
        s=Sum("amount")
    )["s"] or 0
    reversals = ledger_qs_period.filter(entry_type=CompanyBalanceTransaction.EntryType.REVERSAL).aggregate(
        s=Sum("amount")
    )["s"] or 0
    adjustments = ledger_qs_period.filter(entry_type=CompanyBalanceTransaction.EntryType.ADJUSTMENT).aggregate(
        s=Sum("amount")
    )["s"] or 0

    sales_non_cancelled = confirmed_sales(sales_qs_filtered)
    agg = {
        **sales_non_cancelled.aggregate(cnt=Count("id"), total_sell=Sum("sell_price_actual")),
        **paid_sales_only(sales_qs_filtered).aggregate(total_profit=Sum("profit_snapshot")),
    }

    # When the user picked a date range we also expose the *closing*
    # balance as of ``period_to`` so the "current balance" KPI stays
    # meaningful for historical lookups. The signed contribution to the
    # supplier balance per ledger row is +amount for everything except
    # DEDUCTION which is -amount; ADJUSTMENT.amount is already signed.
    balance_as_of = None
    if period_to:
        from django.db.models import Case, F, Value, When

        ledger_up_to = _apply_period(
            company.balance_transactions.all(), None, period_to
        )
        delta = ledger_up_to.aggregate(
            d=Sum(
                Case(
                    When(
                        entry_type=CompanyBalanceTransaction.EntryType.DEDUCTION,
                        then=-F("amount"),
                    ),
                    default=F("amount"),
                )
            )
        )["d"] or 0
        balance_as_of = (company.opening_balance or 0) + delta

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
        "period_from": period_from,
        "period_to": period_to,
        "period_active": period_active,
        "balance_as_of": balance_as_of,
    }
    if request.headers.get("HX-Request"):
        frag = (request.GET.get("partial") or "").strip()
        if frag == "sales":
            return render(request, "reports/partials/company_report_sales.html", ctx)
        if frag == "ledger":
            return render(request, "reports/partials/company_report_ledger.html", ctx)
    return render(request, "reports/company_report.html", ctx)
