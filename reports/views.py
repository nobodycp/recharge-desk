from django import forms
from django.contrib import messages
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate, TruncMonth
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.permissions import management_required
from core.kpi_cache import cached_kpi
from core.pagination import paginate_request
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
from providers.health import alerts as health_alerts
from sales.models import Sale


def _local_today():
    return timezone.localdate()


DASHBOARD_PERIODS = ("day", "month", "all")
DASHBOARD_PERIOD_SESSION_KEY = "dashboard_period"

DASHBOARD_PERIOD_COPY = {
    "day": {
        "sales": _("Today's sales"),
        "profit": _("Today's profit"),
        "loss": _("Today's loss"),
        "chart": _("Sales last 14 days"),
        "chart_avg": _("Avg per day"),
        "chart_end": _("Today"),
        "top": _("Top companies today (by profit)"),
    },
    "month": {
        "sales": _("This month's sales"),
        "profit": _("This month profit"),
        "loss": _("This month's loss"),
        "chart": _("Daily sales this month"),
        "chart_avg": _("Avg per day"),
        "chart_end": _("Today"),
        "top": _("Top companies (this month, by profit)"),
    },
    "all": {
        "sales": _("All-time sales"),
        "profit": _("All-time profit"),
        "loss": _("All-time loss"),
        "chart": _("Monthly sales"),
        "chart_avg": _("Avg per month"),
        "chart_end": _("This month"),
        "top": _("Top companies (all time, by profit)"),
    },
}


def _dashboard_period(request) -> str:
    """Selected dashboard period: ``?period=`` wins and is remembered in the session."""
    requested = request.GET.get("period")
    if requested in DASHBOARD_PERIODS:
        request.session[DASHBOARD_PERIOD_SESSION_KEY] = requested
        return requested
    stored = request.session.get(DASHBOARD_PERIOD_SESSION_KEY)
    return stored if stored in DASHBOARD_PERIODS else "day"


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

    period = _dashboard_period(request)
    # "month" is the calendar month to date, not a rolling 30-day window.
    period_start = {"day": today, "month": month_start, "all": None}[period]
    period_key = f"{period}:{period_start.isoformat() if period_start else 'all'}"

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

    def _in_period(qs, date_field="created_at__date"):
        if period_start is None:
            return qs
        return qs.filter(**{f"{date_field}__gte": period_start})

    def _period_sales():
        return _in_period(confirmed_sales(Sale.objects.all()))

    period_count = cached_kpi(
        f"dashboard:period_count:{period_key}",
        lambda: _period_sales().count(),
    )
    period_volume = cached_kpi(
        f"dashboard:period_volume:{period_key}",
        lambda: _period_sales().aggregate(s=Sum("sell_price_actual"))["s"] or 0,
    )
    period_profit = cached_kpi(
        f"dashboard:period_profit:{period_key}",
        lambda: paid_sales_only(_period_sales()).aggregate(s=Sum("profit_snapshot"))["s"]
        or 0,
    )
    period_expenses = cached_kpi(
        f"dashboard:period_expenses:{period_key}",
        lambda: _in_period(Expense.objects.all(), "date").aggregate(s=Sum("amount"))["s"]
        or 0,
    )
    period_net = (period_profit or 0) - (period_expenses or 0)

    # Supplier balances card — keep live (icons / ordering).
    companies = Company.objects.filter(is_active=True).order_by("name")

    # Phone disconnect KPIs use the viewer's threshold (same as the report).
    from accounts.models import UserProfile
    from reports.stale_phones import reference_disconnect_counts

    try:
        threshold_days = int(request.user.profile.stale_phone_threshold_days)
    except (UserProfile.DoesNotExist, AttributeError, TypeError, ValueError):
        threshold_days = 85
    phone_stats = cached_kpi(
        f"dashboard:phone_lifecycle_v5:{threshold_days}:{today_key}",
        lambda: reference_disconnect_counts(
            threshold_days=threshold_days, due_soon_hours=24
        ),
    )
    phones_active_count = int(phone_stats.get("active", 0) or 0)
    phones_due_soon_count = int(phone_stats.get("due_soon", 0) or 0)
    phones_ended_under_threshold_count = int(
        phone_stats.get("ended_under_threshold", 0) or 0
    )
    phones_overdue_count = int(phone_stats.get("overdue", 0) or 0)
    phones_esim_active_count = int(phone_stats.get("esim_active", 0) or 0)
    phones_lifecycle_total = int(
        phone_stats.get("total", 0)
        or (
            phones_active_count
            + phones_due_soon_count
            + phones_ended_under_threshold_count
            + phones_overdue_count
        )
    )

    def _phone_pct(n: int) -> float:
        if phones_lifecycle_total <= 0:
            return 0.0
        return round(100.0 * n / phones_lifecycle_total, 2)

    # Cumulative conic-gradient stops for the donut (clockwise from 12 o'clock).
    phone_donut_active_end = _phone_pct(phones_active_count)
    phone_donut_due_end = phone_donut_active_end + _phone_pct(phones_due_soon_count)
    phone_donut_grace_end = phone_donut_due_end + _phone_pct(
        phones_ended_under_threshold_count
    )

    period_esim_count = cached_kpi(
        f"dashboard:period_esim_count:{period_key}",
        lambda: _period_sales().filter(is_esim=True).count(),
    )
    period_loss = cached_kpi(
        f"dashboard:period_loss:{period_key}",
        lambda: loss_eligible_sales(_in_period(Sale.objects.all())).aggregate(
            s=Sum("loss_snapshot")
        )["s"]
        or 0,
    )

    # Sales chart follows the period: "day" keeps two weeks of context with
    # today highlighted, "month" shows each day of the calendar month, and
    # "all" buckets by month. Single GROUP BY on the indexed created_at.
    chart_monthly_cap = 24

    def _daily_series(start):
        from datetime import timedelta

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
        for i in range((today - start).days + 1):
            day = start + timedelta(days=i)
            v, c = bucket.get(day, (0.0, 0))
            out.append(
                {
                    "date": day.isoformat(),
                    "tick": str(day.day) if period == "month" else _ar_day_letter[day.weekday()],
                    "volume": v,
                    "count": c,
                }
            )
        return out

    def _monthly_series():
        rows = (
            confirmed_sales(Sale.objects.all())
            .annotate(m=TruncMonth("created_at"))
            .values("m")
            .annotate(volume=Sum("sell_price_actual"), cnt=Count("id"))
            .order_by("m")
        )
        bucket = {
            (r["m"].year, r["m"].month): (float(r["volume"] or 0), int(r["cnt"] or 0))
            for r in rows
            if r["m"] is not None
        }
        if not bucket:
            return []
        first_year, first_month = min(bucket)
        months = []
        y, m = today.year, today.month
        while (y, m) >= (first_year, first_month) and len(months) < chart_monthly_cap:
            months.append((y, m))
            y, m = (y - 1, 12) if m == 1 else (y, m - 1)
        out = []
        for y, m in reversed(months):
            v, c = bucket.get((y, m), (0.0, 0))
            out.append(
                {"date": f"{y}-{m:02d}", "tick": str(m), "volume": v, "count": c}
            )
        return out

    def _chart_series():
        from datetime import timedelta

        if period == "all":
            return _monthly_series()
        start = month_start if period == "month" else today - timedelta(days=13)
        return _daily_series(start)

    def _top_companies():
        rows = (
            paid_sales_only(_period_sales())
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

    chart_series = cached_kpi(
        f"dashboard:chart_series_v4:{period}:{today_key}", _chart_series
    )
    top_companies = cached_kpi(
        f"dashboard:top_companies:{period_key}", _top_companies
    )

    chart_buckets = len(chart_series)
    chart_max_volume = max((d["volume"] for d in chart_series), default=0) or 1
    chart_max_company_profit = max((c["profit"] for c in top_companies), default=0) or 1
    chart_total_volume = sum(d["volume"] for d in chart_series)
    chart_avg_volume = chart_total_volume / chart_buckets if chart_buckets else 0

    return render(
        request,
        "reports/dashboard.html",
        {
            "title": _("Dashboard"),
            "period": period,
            "period_options": [
                ("day", _("Day")),
                ("month", _("Month")),
                ("all", _("All")),
            ],
            "period_copy": DASHBOARD_PERIOD_COPY[period],
            "pending_count": pending_count,
            "awaiting_count": awaiting_count,
            "payment_submissions_awaiting_count": payment_submissions_awaiting_count,
            "customer_debt_total": customer_debt_total,
            "period_count": period_count,
            "period_volume": period_volume,
            "period_profit": period_profit,
            "period_esim_count": period_esim_count,
            "period_loss": period_loss,
            "period_expenses": period_expenses,
            "period_net": period_net,
            "companies": companies,
            "phones_active_count": phones_active_count,
            "phones_due_soon_count": phones_due_soon_count,
            "phones_ended_under_threshold_count": phones_ended_under_threshold_count,
            "phones_overdue_count": phones_overdue_count,
            "phones_esim_active_count": phones_esim_active_count,
            "phones_lifecycle_total": phones_lifecycle_total,
            "phone_donut_active_end": phone_donut_active_end,
            "phone_donut_due_end": phone_donut_due_end,
            "phone_donut_grace_end": phone_donut_grace_end,
            "stale_phone_threshold_days": threshold_days,
            "chart_series": chart_series,
            "top_companies": top_companies,
            "chart_max_volume": chart_max_volume,
            "chart_max_company_profit": chart_max_company_profit,
            "chart_total_volume": chart_total_volume,
            "chart_avg_volume": chart_avg_volume,
            "health_alerts": health_alerts(),
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


def _profit_bar_max(rows, key="profit"):
    vals = [abs(r.get(key) or 0) for r in rows]
    return max(vals) if vals else 1


@management_required
def profit_report(request):
    form = DateRangeForm(request.GET or None)
    qs = confirmed_sales(Sale.objects.all())
    date_from = date_to = None
    if form.is_valid():
        date_from = form.cleaned_data.get("date_from")
        date_to = form.cleaned_data.get("date_to")
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
    qs_paid = paid_sales_only(qs)
    totals = qs_paid.aggregate(
        profit=Sum("profit_snapshot"),
        volume=Sum("sell_price_actual"),
        cnt=Count("id"),
    )
    total_profit = totals["profit"] or 0
    total_volume = totals["volume"] or 0
    total_count = totals["cnt"] or 0
    by_company = list(
        qs_paid.values("company__name")
        .annotate(
            profit=Sum("profit_snapshot"),
            volume=Sum("sell_price_actual"),
            cnt=Count("id"),
        )
        .order_by("-profit")
    )
    by_product = list(
        qs_paid.values(
            "company__name",
            "product__line__name",
            "product__variant_label",
        )
        .annotate(profit=Sum("profit_snapshot"), cnt=Count("id"))
        .order_by("-profit")[:20]
    )
    by_employee = list(
        qs_paid.values("created_by__username")
        .annotate(profit=Sum("profit_snapshot"), cnt=Count("id"))
        .order_by("-profit")
    )
    by_method = list(
        qs_paid.values("payment_method__name")
        .annotate(profit=Sum("profit_snapshot"), cnt=Count("id"))
        .order_by("-profit")
    )
    daily = list(
        qs_paid.annotate(d=TruncDate("created_at"))
        .values("d")
        .annotate(profit=Sum("profit_snapshot"), cnt=Count("id"))
        .order_by("-d")[:31]
    )[::-1]
    monthly = list(
        qs_paid.annotate(m=TruncMonth("created_at"))
        .values("m")
        .annotate(profit=Sum("profit_snapshot"), cnt=Count("id"))
        .order_by("-m")[:12]
    )[::-1]
    return render(
        request,
        "reports/profit_report.html",
        {
            "form": form,
            "title": _("Profit report"),
            "date_from": date_from,
            "date_to": date_to,
            "total_profit": total_profit,
            "total_volume": total_volume,
            "total_count": total_count,
            "by_company": by_company,
            "by_product": by_product,
            "by_employee": by_employee,
            "by_method": by_method,
            "daily": daily,
            "monthly": monthly,
            "max_company_profit": _profit_bar_max(by_company),
            "max_method_profit": _profit_bar_max(by_method),
            "max_employee_profit": _profit_bar_max(by_employee),
            "max_product_profit": _profit_bar_max(by_product),
            "max_daily_profit": _profit_bar_max(daily),
            "max_monthly_profit": _profit_bar_max(monthly),
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


@management_required
def company_report(request, pk):
    """Legacy URL — redirect to the supplier detail page under Companies."""
    url = reverse("companies:company_detail", kwargs={"pk": pk})
    if request.GET:
        url = f"{url}?{request.GET.urlencode()}"
    return redirect(url)
