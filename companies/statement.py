"""Supplier statement: sales grid, balance ledger, KPIs, deposits."""

from __future__ import annotations

from datetime import date as date_type
from decimal import Decimal

from django.contrib import messages
from django.db.models import Case, Count, F, Q, Sum, When
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from core.pagination import paginate_request
from sales.forms import BalanceAdjustmentForm, ManualDepositForm
from sales.ledger_query_utils import apply_ledger_list_ordering, filter_ledger_queryset
from sales.models import CompanyBalanceTransaction, Sale
from sales.query_utils import apply_sale_list_ordering, confirmed_sales, paid_sales_only
from sales.services import record_balance_adjustment, record_manual_deposit

TAB_GENERAL = "general"
TAB_SALES = "sales"
TAB_LEDGER = "ledger"
DEFAULT_TAB = TAB_GENERAL


def _redirect_company_detail(pk, tab):
    url = reverse("companies:company_detail", kwargs={"pk": pk})
    return redirect(f"{url}?tab={tab}")


def parse_company_statement_period(request):
    """Read ``period_from`` / ``period_to`` GET params into date objects."""

    def _parse(s):
        try:
            return date_type.fromisoformat((s or "").strip())
        except (ValueError, TypeError):
            return None

    return _parse(request.GET.get("period_from")), _parse(request.GET.get("period_to"))


def apply_period(qs, date_from, date_to, field="created_at"):
    if date_from:
        qs = qs.filter(**{f"{field}__date__gte": date_from})
    if date_to:
        qs = qs.filter(**{f"{field}__date__lte": date_to})
    return qs


def company_statement_sales_queryset(company, request, *, date_from=None, date_to=None):
    qs = company.sales.select_related(
        "product",
        "product__line",
        "payment_method",
        "created_by",
        "created_by__profile",
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
    qs = apply_period(qs, date_from, date_to)
    return apply_sale_list_ordering(
        request, qs, sort_param="sales_sort", order_param="sales_order"
    )


def company_statement_ledger_queryset(company, request, *, date_from=None, date_to=None):
    qs = company.balance_transactions.select_related("created_by")
    qs = filter_ledger_queryset(qs, request.GET.get("ledger_q") or "")
    qs = apply_period(qs, date_from, date_to)
    return apply_ledger_list_ordering(request, qs)


def active_company_statement_tab(request):
    tab = (request.GET.get("tab") or "").strip()
    if tab in (TAB_GENERAL, TAB_SALES, TAB_LEDGER):
        return tab
    return DEFAULT_TAB


def _ledger_signed_delta(txn: CompanyBalanceTransaction) -> Decimal:
    if txn.entry_type == CompanyBalanceTransaction.EntryType.DEDUCTION:
        return -txn.amount
    return txn.amount


def ledger_balance_snapshots(company) -> dict[int, dict[str, Decimal]]:
    """Running supplier balance before/after each ledger row (chronological)."""
    running = Decimal(company.opening_balance or 0)
    out: dict[int, dict[str, Decimal]] = {}
    for txn in company.balance_transactions.order_by("created_at", "id"):
        before = running
        after = before + _ledger_signed_delta(txn)
        out[txn.pk] = {"before": before, "after": after}
        running = after
    return out


def _sale_operator_label(sale: Sale) -> str:
    if sale.employee_recipient_id:
        return sale.employee_recipient.display_name
    profile = getattr(sale.created_by, "profile", None)
    if profile and profile.display_name:
        return profile.display_name
    return sale.created_by.username


def _annotate_ledger_page(ledger_page, *, balance_snapshots: dict, sale_refs: dict[int, str]):
    for txn in ledger_page:
        snap = balance_snapshots.get(txn.pk, {})
        txn.balance_before = snap.get("before")
        txn.balance_after = snap.get("after")
        if (
            txn.reference_type == CompanyBalanceTransaction.ReferenceType.SALE
            and txn.reference_id
        ):
            txn.movement_reference = sale_refs.get(txn.reference_id) or ""
        elif txn.notes:
            txn.movement_reference = txn.notes
        else:
            txn.movement_reference = ""


def _annotate_sales_page(sales_page, *, balance_snapshots: dict):
    if not sales_page:
        return
    sale_ids = [s.pk for s in sales_page]
    deductions = {
        t.reference_id: t
        for t in CompanyBalanceTransaction.objects.filter(
            company_id=sales_page[0].company_id,
            entry_type=CompanyBalanceTransaction.EntryType.DEDUCTION,
            reference_type=CompanyBalanceTransaction.ReferenceType.SALE,
            reference_id__in=sale_ids,
        )
    }
    for sale in sales_page:
        sale.operator_name = _sale_operator_label(sale)
        txn = deductions.get(sale.pk)
        if txn and txn.pk in balance_snapshots:
            snap = balance_snapshots[txn.pk]
            sale.balance_before = snap["before"]
            sale.balance_after = snap["after"]
        else:
            sale.balance_before = None
            sale.balance_after = None


def handle_company_statement_post(request, company):
    """Process deposit/adjustment POST. Returns redirect response or None."""
    deposit_form = ManualDepositForm(request.POST or None, prefix="dep")
    adj_form = BalanceAdjustmentForm(request.POST or None, prefix="adj")
    if "dep-submit" in request.POST and deposit_form.is_valid():
        record_manual_deposit(
            company=company,
            amount=deposit_form.cleaned_data["amount"],
            notes=deposit_form.cleaned_data.get("notes") or "",
            user=request.user,
        )
        messages.success(request, _("Deposit recorded."))
        return _redirect_company_detail(company.pk, TAB_GENERAL)
    if "adj-submit" in request.POST and adj_form.is_valid():
        record_balance_adjustment(
            company=company,
            signed_amount=adj_form.cleaned_data["signed_amount"],
            notes=adj_form.cleaned_data.get("notes") or "",
            user=request.user,
        )
        messages.success(request, _("Adjustment recorded."))
        return _redirect_company_detail(company.pk, TAB_GENERAL)
    return None


def build_company_statement_context(request, company):
    period_from, period_to = parse_company_statement_period(request)
    period_active = bool(period_from or period_to)
    active_tab = active_company_statement_tab(request)

    ledger_qs_period = apply_period(
        company.balance_transactions.all(), period_from, period_to
    )
    sales_qs_filtered = company_statement_sales_queryset(
        company, request, date_from=period_from, date_to=period_to
    )
    ledger_qs_filtered = company_statement_ledger_queryset(
        company, request, date_from=period_from, date_to=period_to
    )

    sales_page = paginate_request(request, sales_qs_filtered, page_param="sales_page")
    ledger_page = paginate_request(request, ledger_qs_filtered, page_param="ledger_page")

    balance_snapshots = ledger_balance_snapshots(company)
    sale_ref_ids = [
        t.reference_id
        for t in ledger_page
        if t.reference_type == CompanyBalanceTransaction.ReferenceType.SALE
        and t.reference_id
    ]
    sale_refs = dict(
        Sale.objects.filter(pk__in=sale_ref_ids).values_list("pk", "reference_number")
    )
    _annotate_ledger_page(
        ledger_page, balance_snapshots=balance_snapshots, sale_refs=sale_refs
    )
    _annotate_sales_page(sales_page, balance_snapshots=balance_snapshots)

    deposits = ledger_qs_period.filter(
        entry_type=CompanyBalanceTransaction.EntryType.DEPOSIT
    ).aggregate(s=Sum("amount"))["s"] or 0
    consumed = ledger_qs_period.filter(
        entry_type=CompanyBalanceTransaction.EntryType.DEDUCTION
    ).aggregate(s=Sum("amount"))["s"] or 0
    reversals = ledger_qs_period.filter(
        entry_type=CompanyBalanceTransaction.EntryType.REVERSAL
    ).aggregate(s=Sum("amount"))["s"] or 0
    adjustments = ledger_qs_period.filter(
        entry_type=CompanyBalanceTransaction.EntryType.ADJUSTMENT
    ).aggregate(s=Sum("amount"))["s"] or 0

    sales_non_cancelled = confirmed_sales(sales_qs_filtered)
    agg = {
        **sales_non_cancelled.aggregate(cnt=Count("id"), total_sell=Sum("sell_price_actual")),
        **paid_sales_only(sales_qs_filtered).aggregate(total_profit=Sum("profit_snapshot")),
    }

    balance_as_of = None
    if period_to:
        ledger_up_to = apply_period(
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

    return {
        "company": company,
        "ledger_page": ledger_page,
        "sales_page": sales_page,
        "deposits_total": deposits,
        "consumed_total": consumed,
        "reversals_total": reversals or 0,
        "adjustments_total": adjustments or 0,
        "agg": agg,
        "deposit_form": ManualDepositForm(request.POST or None, prefix="dep"),
        "adj_form": BalanceAdjustmentForm(request.POST or None, prefix="adj"),
        "title": company.name,
        "sales_sort": request.GET.get("sales_sort") or "created_at",
        "sales_order": (request.GET.get("sales_order") or "desc").lower(),
        "ledger_sort": request.GET.get("ledger_sort") or "created_at",
        "ledger_order": (request.GET.get("ledger_order") or "desc").lower(),
        "period_from": period_from,
        "period_to": period_to,
        "period_active": period_active,
        "balance_as_of": balance_as_of,
        "active_tab": active_tab,
        "tab_general": TAB_GENERAL,
        "tab_sales": TAB_SALES,
        "tab_ledger": TAB_LEDGER,
        "sales_table_full": active_tab == TAB_SALES,
        "ledger_table_full": active_tab == TAB_LEDGER,
    }


def render_company_statement(request, company):
    if request.method == "POST":
        redirect_resp = handle_company_statement_post(request, company)
        if redirect_resp is not None:
            return redirect_resp

    ctx = build_company_statement_context(request, company)

    if request.headers.get("HX-Request"):
        frag = (request.GET.get("partial") or "").strip()
        if frag == TAB_SALES:
            return render(
                request, "reports/partials/company_report_sales.html", ctx
            )
        if frag == TAB_LEDGER:
            return render(
                request, "reports/partials/company_report_ledger.html", ctx
            )
        if frag == TAB_GENERAL:
            return render(
                request, "companies/partials/company_detail_general.html", ctx
            )

    return render(request, "companies/company_detail.html", ctx)
