from decimal import Decimal

from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from accounts.permissions import management_required
from core.pagination import paginate_request
from employees.forms import EmployeeAdjustmentForm, EmployeeProfileForm
from employees.models import EmployeeLedgerEntry, EmployeeProfile
from employees.services import (
    accrue_salaries_for_month,
    create_adjustment,
    default_accrual_month,
)


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
    ledger_page = paginate_request(
        request,
        employee.ledger_entries.select_related("reference_sale", "created_by"),
    )
    sales_payments = employee.ledger_entries.filter(
        entry_type=EmployeeLedgerEntry.EntryType.SALES_PAYMENT_RECEIVED
    ).select_related("reference_sale")[:20]

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

    return render(
        request,
        "employees/employee_detail.html",
        {
            "employee": employee,
            "ledger_page": ledger_page,
            "sales_payments": sales_payments,
            "adj_form": adj_form,
            "title": employee.display_name,
        },
    )


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
