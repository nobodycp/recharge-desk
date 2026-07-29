from __future__ import annotations

from calendar import monthrange
from datetime import date
from decimal import Decimal
from typing import Optional

from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from employees.models import EmployeeLedgerEntry, EmployeeProfile

SALARY_EXPENSE_CATEGORY = "Salaries"


def _ensure_expense_for_salary_accrual(
    *, entry: EmployeeLedgerEntry, user=None
):
    """Create or sync the linked expense row for a salary accrual ledger entry."""
    from django.utils.translation import gettext as gettext_now

    from expenses.models import Expense

    if entry.entry_type != EmployeeLedgerEntry.EntryType.SALARY_ACCRUAL:
        raise ValueError(_("Only salary accrual rows can create expenses."))
    if not entry.salary_month:
        raise ValueError(_("Salary accrual row is missing salary month."))

    employee = entry.employee
    month_label = entry.salary_month.strftime("%Y-%m")
    title = gettext_now("Salary: %(name)s — %(month)s") % {
        "name": employee.display_name,
        "month": month_label,
    }
    notes = gettext_now("Auto-created from salary accrual.")
    category = gettext_now(SALARY_EXPENSE_CATEGORY)

    if entry.expense_id:
        expense = entry.expense
        changed = False
        if expense.amount != entry.amount:
            expense.amount = entry.amount
            changed = True
        if expense.title != title:
            expense.title = title
            changed = True
        if expense.date != entry.salary_month:
            expense.date = entry.salary_month
            changed = True
        if expense.category != category:
            expense.category = category
            changed = True
        if changed:
            expense.save(update_fields=["amount", "title", "date", "category", "updated_at"])
        return expense

    expense = Expense.objects.create(
        title=title,
        category=category,
        amount=entry.amount,
        date=entry.salary_month,
        notes=notes,
        created_by=user,
    )
    entry.expense = expense
    entry.save(update_fields=["expense"])
    return expense


def get_acting_employee_profile(user) -> EmployeeProfile | None:
    """Active payroll profile for the logged-in user, if any."""
    if not user or not getattr(user, "is_authenticated", False):
        return None
    profile = getattr(user, "employee_profile", None)
    if profile and profile.is_active:
        return profile
    return None


def _apply_balance_delta(employee: EmployeeProfile, delta: Decimal) -> None:
    EmployeeProfile.objects.filter(pk=employee.pk).update(
        current_balance=F("current_balance") + delta
    )
    employee.refresh_from_db(fields=["current_balance"])


def compute_balance_from_ledger(employee: EmployeeProfile) -> Decimal:
    total = employee.ledger_entries.aggregate(s=Sum("amount"))["s"]
    return total if total is not None else Decimal("0")


@transaction.atomic
def accrue_salary_for_month(
    *,
    employee: EmployeeProfile,
    salary_month: date,
    user=None,
    force: bool = False,
) -> Optional[EmployeeLedgerEntry]:
    """Credit one month's salary. Idempotent unless ``force`` is True."""
    if salary_month.day != 1:
        salary_month = salary_month.replace(day=1)
    if employee.monthly_salary <= 0:
        return None
    employee_locked = EmployeeProfile.objects.select_for_update().get(pk=employee.pk)
    if not employee_locked.is_active:
        return None
    existing = EmployeeLedgerEntry.objects.filter(
        employee=employee_locked,
        entry_type=EmployeeLedgerEntry.EntryType.SALARY_ACCRUAL,
        salary_month=salary_month,
    ).first()
    if existing and not force:
        _ensure_expense_for_salary_accrual(entry=existing, user=user)
        return existing
    if existing and force:
        delta = employee_locked.monthly_salary - existing.amount
        if delta == 0:
            _ensure_expense_for_salary_accrual(entry=existing, user=user)
            return existing
        existing.amount = employee_locked.monthly_salary
        existing.save(update_fields=["amount"])
        _apply_balance_delta(employee_locked, delta)
        _ensure_expense_for_salary_accrual(entry=existing, user=user)
        return existing

    entry = EmployeeLedgerEntry.objects.create(
        employee=employee_locked,
        entry_type=EmployeeLedgerEntry.EntryType.SALARY_ACCRUAL,
        amount=employee_locked.monthly_salary,
        salary_month=salary_month,
        notes="",
        created_by=user,
    )
    _apply_balance_delta(employee_locked, employee_locked.monthly_salary)
    _ensure_expense_for_salary_accrual(entry=entry, user=user)
    return entry


def accrue_salaries_for_month(
    *,
    salary_month: date,
    user=None,
) -> int:
    """Accrue salary for every active employee. Returns count of new rows."""
    if salary_month.day != 1:
        salary_month = salary_month.replace(day=1)
    created = 0
    for emp in EmployeeProfile.objects.filter(is_active=True, monthly_salary__gt=0):
        before = EmployeeLedgerEntry.objects.filter(
            employee=emp,
            entry_type=EmployeeLedgerEntry.EntryType.SALARY_ACCRUAL,
            salary_month=salary_month,
        ).exists()
        row = accrue_salary_for_month(employee=emp, salary_month=salary_month, user=user)
        if row and not before:
            created += 1
    return created


@transaction.atomic
def record_sales_payment_received(*, sale, user) -> EmployeeLedgerEntry:
    """Debit employee when a sale paid via them is marked paid."""
    if not sale.paid_via_employee or not sale.employee_recipient_id:
        raise ValueError(_("Sale is not an employee payment sale."))
    if EmployeeLedgerEntry.objects.filter(
        reference_sale=sale,
        entry_type=EmployeeLedgerEntry.EntryType.SALES_PAYMENT_RECEIVED,
    ).exists():
        return EmployeeLedgerEntry.objects.get(
            reference_sale=sale,
            entry_type=EmployeeLedgerEntry.EntryType.SALES_PAYMENT_RECEIVED,
        )

    employee_locked = EmployeeProfile.objects.select_for_update().get(
        pk=sale.employee_recipient_id
    )
    amount = -sale.sell_price_actual
    entry = EmployeeLedgerEntry.objects.create(
        employee=employee_locked,
        entry_type=EmployeeLedgerEntry.EntryType.SALES_PAYMENT_RECEIVED,
        amount=amount,
        reference_sale=sale,
        payer_name=sale.payer_name,
        phone=sale.reference_number,
        notes=sale.notes or "",
        created_by=user,
    )
    _apply_balance_delta(employee_locked, amount)
    return entry


@transaction.atomic
def reverse_sales_payment_for_sale(*, sale) -> None:
    """Undo employee ledger row when a paid-via-employee sale is deleted."""
    rows = list(
        EmployeeLedgerEntry.objects.filter(
            reference_sale=sale,
            entry_type=EmployeeLedgerEntry.EntryType.SALES_PAYMENT_RECEIVED,
        )
    )
    if not rows:
        return
    employee_locked = EmployeeProfile.objects.select_for_update().get(
        pk=sale.employee_recipient_id
    )
    delta = Decimal("0")
    for row in rows:
        delta -= row.amount
    EmployeeLedgerEntry.objects.filter(pk__in=[r.pk for r in rows]).delete()
    if delta:
        _apply_balance_delta(employee_locked, delta)


@transaction.atomic
def record_customer_payment_received(*, payment, user) -> EmployeeLedgerEntry:
    """Debit employee when a customer payment paid via them is applied."""
    if not payment.paid_via_employee or not payment.employee_recipient_id:
        raise ValueError(_("Payment is not an employee-held customer payment."))
    existing = EmployeeLedgerEntry.objects.filter(
        reference_customer_payment=payment,
        entry_type=EmployeeLedgerEntry.EntryType.CUSTOMER_PAYMENT_RECEIVED,
    ).first()
    if existing:
        return existing

    employee_locked = EmployeeProfile.objects.select_for_update().get(
        pk=payment.employee_recipient_id
    )
    amount = -Decimal(payment.amount)
    phone = ""
    customer = getattr(payment, "customer", None)
    if customer is not None:
        first_phone = customer.phones.order_by("pk").values_list("phone", flat=True).first()
        phone = first_phone or ""
    entry = EmployeeLedgerEntry.objects.create(
        employee=employee_locked,
        entry_type=EmployeeLedgerEntry.EntryType.CUSTOMER_PAYMENT_RECEIVED,
        amount=amount,
        reference_customer_payment=payment,
        payer_name=customer.name if customer is not None else "",
        phone=phone,
        notes=payment.notes or "",
        created_by=user,
    )
    _apply_balance_delta(employee_locked, amount)
    return entry


@transaction.atomic
def reverse_customer_payment_received(*, payment) -> None:
    """Undo employee ledger row when an employee-held customer payment is deleted."""
    rows = list(
        EmployeeLedgerEntry.objects.filter(
            reference_customer_payment=payment,
            entry_type=EmployeeLedgerEntry.EntryType.CUSTOMER_PAYMENT_RECEIVED,
        )
    )
    if not rows:
        return
    employee_id = payment.employee_recipient_id or rows[0].employee_id
    employee_locked = EmployeeProfile.objects.select_for_update().get(pk=employee_id)
    delta = Decimal("0")
    for row in rows:
        delta -= row.amount
    EmployeeLedgerEntry.objects.filter(pk__in=[r.pk for r in rows]).delete()
    if delta:
        _apply_balance_delta(employee_locked, delta)


@transaction.atomic
def sync_sales_payment_ledger_for_sale(*, sale, user) -> None:
    """Refresh employee ledger row after a paid employee-payment sale is edited."""
    from sales.models import Sale

    if not sale.paid_via_employee or sale.status != Sale.Status.PAID:
        return
    reverse_sales_payment_for_sale(sale=sale)
    record_sales_payment_received(sale=sale, user=user)


@transaction.atomic
def create_adjustment(
    *,
    employee: EmployeeProfile,
    amount: Decimal,
    notes: str,
    user,
) -> EmployeeLedgerEntry:
    if amount == 0:
        raise ValueError(_("Adjustment amount cannot be zero."))
    employee_locked = EmployeeProfile.objects.select_for_update().get(pk=employee.pk)
    entry = EmployeeLedgerEntry.objects.create(
        employee=employee_locked,
        entry_type=EmployeeLedgerEntry.EntryType.ADJUSTMENT,
        amount=amount,
        notes=notes.strip(),
        created_by=user,
    )
    _apply_balance_delta(employee_locked, amount)
    return entry


@transaction.atomic
def delete_ledger_entry(*, entry: EmployeeLedgerEntry) -> EmployeeProfile:
    """Remove one employee ledger row and reverse its balance impact."""
    locked_entry = (
        EmployeeLedgerEntry.objects.select_for_update()
        .select_related("employee")
        .get(pk=entry.pk)
    )
    employee_locked = EmployeeProfile.objects.select_for_update().get(
        pk=locked_entry.employee_id
    )
    expense_id = locked_entry.expense_id
    amount = locked_entry.amount
    locked_entry.delete()
    if expense_id:
        from expenses.models import Expense

        Expense.objects.filter(pk=expense_id).delete()
    _apply_balance_delta(employee_locked, -amount)
    return employee_locked


def month_bounds(year: int, month: int) -> tuple[date, date]:
    last = monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def default_accrual_month() -> date:
    today = timezone.localdate()
    return today.replace(day=1)
