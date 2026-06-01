from decimal import Decimal

from django.contrib import messages
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from accounts.permissions import management_required
from core.pagination import paginate_request
from employees.forms import (
    EmployeeAdjustmentForm,
    EmployeeLedgerFilterForm,
    EmployeeProfileForm,
    EmployeeSalesPaymentFilterForm,
)
from employees.models import EmployeeLedgerEntry, EmployeeProfile
from employees.services import (
    accrue_salaries_for_month,
    create_adjustment,
    default_accrual_month,
    delete_ledger_entry,
)

TAB_PAYMENTS = "payments"
TAB_LEDGER = "ledger"
LEDGER_SORT_FIELDS = {
    "created_at": "created_at",
    "type": "entry_type",
    "amount": "amount",
    "details": "phone",
}
PAYMENTS_SORT_FIELDS = {
    "created_at": "created_at",
    "ref": "phone",
    "payer": "payer_name",
    "amount": "amount",
    "company": "reference_sale__company__name",
    "product": "reference_sale__product__line__name",
    "notes": "notes",
}


def _employee_active_tab(request):
    tab = (request.GET.get("tab") or "").strip()
    if tab in (TAB_PAYMENTS, TAB_LEDGER):
        return tab
    return TAB_LEDGER


def _employee_sort(request, *, sort_param, order_param, default_sort, default_order, fields):
    sort = (request.GET.get(sort_param) or default_sort).strip()
    order = (request.GET.get(order_param) or default_order).lower()
    if sort not in fields:
        sort = default_sort
    if order not in ("asc", "desc"):
        order = default_order
    return sort, order


def _apply_employee_ordering(qs, *, sort, order, fields):
    prefix = "" if order == "asc" else "-"
    field = fields[sort]
    return qs.order_by(f"{prefix}{field}", f"{prefix}pk")


@management_required
def employee_list(request):
    qs = EmployeeProfile.objects.select_related("user", "user__profile")
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(user__username__icontains=q)
            | Q(user__profile__full_name__icontains=q)
        )
    qs = qs.order_by("-is_active", "user__username")
    page_obj = paginate_request(request, qs)
    return render(
        request,
        "employees/employee_list.html",
        {
            "page_obj": page_obj,
            "title": _("Employees"),
            "q": q,
            "accrual_month": default_accrual_month(),
        },
    )


@management_required
def employee_create(request):
    form = EmployeeProfileForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Employee saved."))
        return redirect("employees:employee_list")
    return render(
        request,
        "employees/employee_form.html",
        {"form": form, "title": _("New employee")},
    )


@management_required
def employee_edit(request, pk):
    employee = get_object_or_404(EmployeeProfile, pk=pk)
    form = EmployeeProfileForm(request.POST or None, instance=employee)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Employee updated."))
        return redirect("employees:employee_detail", pk=employee.pk)
    return render(
        request,
        "employees/employee_form.html",
        {"form": form, "title": _("Edit employee"), "employee": employee},
    )


@management_required
def employee_detail(request, pk):
    employee = get_object_or_404(
        EmployeeProfile.objects.select_related("user", "user__profile"),
        pk=pk,
    )
    active_tab = _employee_active_tab(request)

    ledger_qs = employee.ledger_entries.select_related("reference_sale", "created_by")
    ledger_filter_form = EmployeeLedgerFilterForm(request.GET or None)
    ledger_filter_data = (
        ledger_filter_form.cleaned_data if ledger_filter_form.is_valid() else {}
    )
    ledger_filter_active = sum(
        1 for name in ledger_filter_form.fields if ledger_filter_data.get(name)
    )
    ledger_q = (ledger_filter_data.get("ledger_q") or "").strip()
    if ledger_q:
        ledger_qs = ledger_qs.filter(
            Q(phone__icontains=ledger_q)
            | Q(payer_name__icontains=ledger_q)
            | Q(notes__icontains=ledger_q)
            | Q(reference_sale__reference_number__icontains=ledger_q)
            | Q(reference_sale__payer_name__icontains=ledger_q)
        )
    if entry_type := ledger_filter_data.get("ledger_entry_type"):
        ledger_qs = ledger_qs.filter(entry_type=entry_type)
    if date_from := ledger_filter_data.get("ledger_date_from"):
        ledger_qs = ledger_qs.filter(created_at__date__gte=date_from)
    if date_to := ledger_filter_data.get("ledger_date_to"):
        ledger_qs = ledger_qs.filter(created_at__date__lte=date_to)
    ledger_sort, ledger_order = _employee_sort(
        request,
        sort_param="ledger_sort",
        order_param="ledger_order",
        default_sort="created_at",
        default_order="desc",
        fields=LEDGER_SORT_FIELDS,
    )
    ledger_qs = _apply_employee_ordering(
        ledger_qs,
        sort=ledger_sort,
        order=ledger_order,
        fields=LEDGER_SORT_FIELDS,
    )
    ledger_page = paginate_request(request, ledger_qs, page_param="ledger_page")

    payments_qs = employee.ledger_entries.filter(
        entry_type=EmployeeLedgerEntry.EntryType.SALES_PAYMENT_RECEIVED
    ).select_related(
        "reference_sale",
        "reference_sale__company",
        "reference_sale__product",
        "reference_sale__product__line",
        "created_by",
    )
    payments_filter_form = EmployeeSalesPaymentFilterForm(request.GET or None)
    payments_filter_data = (
        payments_filter_form.cleaned_data if payments_filter_form.is_valid() else {}
    )
    payments_filter_active = sum(
        1 for name in payments_filter_form.fields if payments_filter_data.get(name)
    )
    payments_q = (payments_filter_data.get("payments_q") or "").strip()
    if payments_q:
        payments_qs = payments_qs.filter(
            Q(phone__icontains=payments_q)
            | Q(payer_name__icontains=payments_q)
            | Q(notes__icontains=payments_q)
            | Q(reference_sale__reference_number__icontains=payments_q)
            | Q(reference_sale__payer_name__icontains=payments_q)
        )
    if date_from := payments_filter_data.get("payments_date_from"):
        payments_qs = payments_qs.filter(created_at__date__gte=date_from)
    if date_to := payments_filter_data.get("payments_date_to"):
        payments_qs = payments_qs.filter(created_at__date__lte=date_to)
    payments_sort, payments_order = _employee_sort(
        request,
        sort_param="payments_sort",
        order_param="payments_order",
        default_sort="created_at",
        default_order="desc",
        fields=PAYMENTS_SORT_FIELDS,
    )
    payments_qs = _apply_employee_ordering(
        payments_qs,
        sort=payments_sort,
        order=payments_order,
        fields=PAYMENTS_SORT_FIELDS,
    )
    sales_payments_page = paginate_request(
        request,
        payments_qs,
        page_param="payments_page",
    )

    adj_form = EmployeeAdjustmentForm()
    if request.method == "POST" and request.POST.get("form_kind") == "adjustment":
        adj_form = EmployeeAdjustmentForm(request.POST)
        if adj_form.is_valid():
            try:
                create_adjustment(
                    employee=employee,
                    amount=adj_form.cleaned_data["amount"],
                    notes=adj_form.cleaned_data.get("notes") or "",
                    user=request.user,
                )
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, _("Adjustment recorded."))
                return redirect("employees:employee_detail", pk=employee.pk)

    ctx = {
        "employee": employee,
        "active_tab": active_tab,
        "tab_payments": TAB_PAYMENTS,
        "tab_ledger": TAB_LEDGER,
        "ledger_page": ledger_page,
        "ledger_sort": ledger_sort,
        "ledger_order": ledger_order,
        "ledger_filter_form": ledger_filter_form,
        "ledger_filter_active": ledger_filter_active,
        "sales_payments_page": sales_payments_page,
        "payments_sort": payments_sort,
        "payments_order": payments_order,
        "payments_filter_form": payments_filter_form,
        "payments_filter_active": payments_filter_active,
        "adj_form": adj_form,
        "title": employee.display_name,
    }
    if request.headers.get("HX-Request") == "true":
        frag = (request.GET.get("partial") or "").strip()
        if frag == TAB_PAYMENTS:
            return render(
                request,
                "employees/partials/employee_payments_results.html",
                ctx,
            )
        if frag == TAB_LEDGER:
            return render(
                request,
                "employees/partials/employee_ledger_results.html",
                ctx,
            )

    return render(
        request,
        "employees/employee_detail.html",
        ctx,
    )


@management_required
@require_POST
def employee_ledger_delete(request, pk, entry_pk):
    employee = get_object_or_404(EmployeeProfile, pk=pk)
    entry = get_object_or_404(EmployeeLedgerEntry, pk=entry_pk, employee=employee)
    delete_ledger_entry(entry=entry)
    messages.success(request, _("Ledger entry deleted."))
    if request.headers.get("HX-Request") == "true":
        next_url = f"{reverse('employees:employee_detail', args=[employee.pk])}?tab={TAB_LEDGER}"
        response = HttpResponse(status=204)
        response["HX-Redirect"] = next_url
        return response
    fallback = request.META.get("HTTP_REFERER") or "employees:employee_detail"
    if isinstance(fallback, str) and fallback.startswith("/"):
        return redirect(fallback)
    return redirect("employees:employee_detail", pk=employee.pk)


@management_required
@require_POST
def employee_run_salary_accrual(request):
    month_str = (request.POST.get("salary_month") or "").strip()
    if month_str:
        try:
            year_s, month_s = month_str.split("-", 1)
            accrual_month = default_accrual_month().replace(
                year=int(year_s), month=int(month_s), day=1
            )
        except (ValueError, TypeError):
            messages.error(request, _("Invalid month."))
            return redirect("employees:employee_list")
    else:
        accrual_month = default_accrual_month()

    count = accrue_salaries_for_month(salary_month=accrual_month, user=request.user)
    messages.success(
        request,
        _("Salary accrual complete: %(count)s new entries for %(month)s.")
        % {"count": count, "month": accrual_month.strftime("%Y-%m")},
    )
    nxt = (request.POST.get("next") or "").strip()
    if nxt.startswith("/") and not nxt.startswith("//"):
        return redirect(nxt)
    return redirect("employees:employee_list")
