from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserProfile
from companies.models import Company, Product, ProductLine
from employees.models import EmployeeLedgerEntry, EmployeeProfile
from expenses.models import Expense
from employees.services import (
    accrue_salary_for_month,
    accrue_salaries_for_month,
    compute_balance_from_ledger,
    create_adjustment,
    delete_ledger_entry,
    record_sales_payment_received,
)
from sales.models import PaymentMethod, Sale
from sales.services import create_sale, mark_sale_paid

User = get_user_model()


class EmployeePayrollTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.mgmt = User.objects.create_user(username="mgmt", password="x")
        UserProfile.objects.update_or_create(
            user=cls.mgmt,
            defaults={"role": UserProfile.Role.MANAGEMENT, "full_name": "Manager"},
        )
        cls.emp_user = User.objects.create_user(username="cashier", password="x")
        UserProfile.objects.update_or_create(
            user=cls.emp_user,
            defaults={"role": UserProfile.Role.EMPLOYEE, "full_name": "Cashier"},
        )
        cls.recipient_user = User.objects.create_user(username="holder", password="x")
        UserProfile.objects.update_or_create(
            user=cls.recipient_user,
            defaults={"role": UserProfile.Role.EMPLOYEE, "full_name": "Holder"},
        )
        cls.employee = EmployeeProfile.objects.create(
            user=cls.recipient_user,
            monthly_salary=Decimal("3000"),
        )
        cls.company = Company.objects.create(
            name="Co",
            opening_balance=Decimal("1000"),
            current_balance=Decimal("1000"),
        )
        cls.line = ProductLine.objects.create(company=cls.company, name="Line")
        cls.product = Product.objects.create(
            line=cls.line,
            variant_label="Pkg",
            cost_price=Decimal("5"),
            default_sell_price=Decimal("10"),
        )
        cls.pm = PaymentMethod.objects.create(name="Cash")

    def test_salary_accrual_idempotent(self):
        month = timezone.localdate().replace(day=1)
        first = accrue_salary_for_month(employee=self.employee, salary_month=month, user=self.mgmt)
        self.assertIsNotNone(first)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.current_balance, Decimal("3000"))
        second = accrue_salary_for_month(employee=self.employee, salary_month=month, user=self.mgmt)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            EmployeeLedgerEntry.objects.filter(
                employee=self.employee,
                entry_type=EmployeeLedgerEntry.EntryType.SALARY_ACCRUAL,
            ).count(),
            1,
        )
        self.assertEqual(Expense.objects.count(), 1)
        expense = Expense.objects.get()
        self.assertEqual(expense.amount, Decimal("3000"))
        self.assertEqual(expense.date, month)
        self.assertEqual(Expense.objects.count(), 1)

    def test_salary_accrual_creates_expense_with_employee_title(self):
        month = timezone.localdate().replace(day=1)
        entry = accrue_salary_for_month(employee=self.employee, salary_month=month, user=self.mgmt)
        self.assertIsNotNone(entry.expense_id)
        expense = entry.expense
        self.assertIn("Holder", expense.title)
        self.assertIn(month.strftime("%Y-%m"), expense.title)

    def test_bulk_accrual_command_month(self):
        month = timezone.localdate().replace(day=1)
        created = accrue_salaries_for_month(salary_month=month, user=self.mgmt)
        self.assertEqual(created, 1)
        created_again = accrue_salaries_for_month(salary_month=month, user=self.mgmt)
        self.assertEqual(created_again, 0)

    def test_sales_payment_creates_ledger_with_payer_phone(self):
        sale = create_sale(
            company=self.company,
            product=self.product,
            reference_number="0501234567",
            payer_name="Ahmad",
            payment_method=None,
            sell_price_actual=Decimal("25"),
            notes="",
            user=self.emp_user,
            paid_via_employee=True,
            employee_recipient=self.employee,
        )
        mark_sale_paid(sale=sale, user=self.mgmt)
        entry = EmployeeLedgerEntry.objects.get(reference_sale=sale)
        self.assertEqual(entry.entry_type, EmployeeLedgerEntry.EntryType.SALES_PAYMENT_RECEIVED)
        self.assertEqual(entry.amount, Decimal("-25"))
        self.assertEqual(entry.payer_name, "Ahmad")
        self.assertEqual(entry.phone, "0501234567")
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.current_balance, Decimal("-25"))
        self.assertEqual(compute_balance_from_ledger(self.employee), Decimal("-25"))

    def test_record_sales_payment_idempotent(self):
        sale = create_sale(
            company=self.company,
            product=self.product,
            reference_number="0509999999",
            payer_name="Sara",
            payment_method=None,
            sell_price_actual=Decimal("10"),
            notes="",
            user=self.emp_user,
            paid_via_employee=True,
            employee_recipient=self.employee,
        )
        sale.status = Sale.Status.PAID
        sale.save(update_fields=["status"])
        record_sales_payment_received(sale=sale, user=self.mgmt)
        record_sales_payment_received(sale=sale, user=self.mgmt)
        self.assertEqual(
            EmployeeLedgerEntry.objects.filter(
                reference_sale=sale,
                entry_type=EmployeeLedgerEntry.EntryType.SALES_PAYMENT_RECEIVED,
            ).count(),
            1,
        )

    def test_sales_payment_uses_logged_in_employee_profile(self):
        EmployeeProfile.objects.create(user=self.emp_user, monthly_salary=Decimal("0"))
        self.client.login(username="cashier", password="x")
        resp = self.client.post(
            reverse("sales:employee_entry"),
            {
                "company": self.company.pk,
                "product": self.product.pk,
                "reference_number": "0501111111",
                "payer_name": "Ali",
                "sell_price_actual": "15",
                "paid_via_employee": "1",
            },
        )
        self.assertEqual(resp.status_code, 302)
        sale = Sale.objects.latest("pk")
        self.assertTrue(sale.paid_via_employee)
        self.assertEqual(sale.employee_recipient.user_id, self.emp_user.pk)

    def test_management_employee_list_requires_management(self):
        self.client.login(username="cashier", password="x")
        resp = self.client.get(reverse("employees:employee_list"))
        self.assertEqual(resp.status_code, 302)
        self.client.login(username="mgmt", password="x")
        resp = self.client.get(reverse("employees:employee_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Holder")

        resp = self.client.get(reverse("employees:employee_detail", args=[self.employee.pk]))
        self.assertContains(resp, "rd-section-tabs")
        self.assertContains(resp, "employee-ledger-grid")

    def test_delete_ledger_entry_reverses_balance_and_salary_expense(self):
        month = timezone.localdate().replace(day=1)
        entry = accrue_salary_for_month(
            employee=self.employee,
            salary_month=month,
            user=self.mgmt,
        )
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.current_balance, Decimal("3000"))
        self.assertEqual(Expense.objects.count(), 1)

        delete_ledger_entry(entry=entry)

        self.employee.refresh_from_db()
        self.assertEqual(self.employee.current_balance, Decimal("0"))
        self.assertFalse(EmployeeLedgerEntry.objects.filter(pk=entry.pk).exists())
        self.assertEqual(Expense.objects.count(), 0)

    def test_delete_ledger_entry_does_not_lock_nullable_expense_join(self):
        entry = create_adjustment(
            employee=self.employee,
            amount=Decimal("15"),
            notes="manual without expense",
            user=self.mgmt,
        )

        with connection.execute_wrapper(self._assert_no_for_update_outer_expense_join):
            delete_ledger_entry(entry=entry)

        self.assertFalse(EmployeeLedgerEntry.objects.filter(pk=entry.pk).exists())

    def _assert_no_for_update_outer_expense_join(self, execute, sql, params, many, context):
        normalized = " ".join(sql.lower().split())
        self.assertNotIn("left outer join", normalized)
        return execute(sql, params, many, context)

    def test_employee_detail_filters_ledger_entries(self):
        create_adjustment(employee=self.employee, amount=Decimal("10"), notes="bonus", user=self.mgmt)
        create_adjustment(employee=self.employee, amount=Decimal("-5"), notes="cashbox", user=self.mgmt)
        self.client.login(username="mgmt", password="x")

        resp = self.client.get(
            reverse("employees:employee_detail", args=[self.employee.pk]),
            {"ledger_q": "bonus"},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "bonus")
        self.assertNotContains(resp, "cashbox")

    def test_employee_detail_sorts_ledger_entries_by_amount(self):
        create_adjustment(
            employee=self.employee,
            amount=Decimal("10"),
            notes="ledger-low-note",
            user=self.mgmt,
        )
        create_adjustment(
            employee=self.employee,
            amount=Decimal("30"),
            notes="ledger-high-note",
            user=self.mgmt,
        )
        self.client.login(username="mgmt", password="x")

        resp = self.client.get(
            reverse("employees:employee_detail", args=[self.employee.pk]),
            {"ledger_sort": "amount", "ledger_order": "desc"},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "rd-datagrid-sort")
        self.assertLess(
            resp.content.decode().index("ledger-high-note"),
            resp.content.decode().index("ledger-low-note"),
        )

    def test_employee_detail_sorts_payments_by_payer(self):
        EmployeeLedgerEntry.objects.create(
            employee=self.employee,
            entry_type=EmployeeLedgerEntry.EntryType.SALES_PAYMENT_RECEIVED,
            amount=Decimal("-20"),
            phone="222",
            payer_name="Zed",
            created_by=self.mgmt,
        )
        EmployeeLedgerEntry.objects.create(
            employee=self.employee,
            entry_type=EmployeeLedgerEntry.EntryType.SALES_PAYMENT_RECEIVED,
            amount=Decimal("-10"),
            phone="111",
            payer_name="Adam",
            created_by=self.mgmt,
        )
        self.client.login(username="mgmt", password="x")

        resp = self.client.get(
            reverse("employees:employee_detail", args=[self.employee.pk]),
            {
                "tab": "payments",
                "payments_sort": "payer",
                "payments_order": "asc",
            },
        )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "rd-datagrid-sort")
        self.assertLess(
            resp.content.decode().index("Adam"),
            resp.content.decode().index("Zed"),
        )

    def test_employee_payments_table_shows_sale_context_columns(self):
        sale = create_sale(
            company=self.company,
            product=self.product,
            reference_number="0591111111",
            payer_name="Context Payer",
            payment_method=None,
            sell_price_actual=Decimal("40"),
            notes="context note",
            user=self.emp_user,
            paid_via_employee=True,
            employee_recipient=self.employee,
        )
        mark_sale_paid(sale=sale, user=self.mgmt)
        self.client.login(username="mgmt", password="x")

        resp = self.client.get(
            reverse("employees:employee_detail", args=[self.employee.pk]),
            {"tab": "payments"},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Co")
        self.assertContains(resp, "Line — Pkg")
        self.assertContains(resp, "context note")
        self.assertContains(resp, "Context Payer")
        self.assertContains(resp, "40")

    def test_management_can_delete_employee_ledger_entry_from_detail(self):
        entry = create_adjustment(
            employee=self.employee,
            amount=Decimal("15"),
            notes="manual",
            user=self.mgmt,
        )
        self.client.login(username="mgmt", password="x")

        resp = self.client.post(
            reverse(
                "employees:employee_ledger_delete",
                args=[self.employee.pk, entry.pk],
            )
        )

        self.assertEqual(resp.status_code, 302)
        self.assertFalse(EmployeeLedgerEntry.objects.filter(pk=entry.pk).exists())
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.current_balance, Decimal("0"))

    def test_htmx_delete_employee_ledger_entry_redirects_to_fresh_detail(self):
        entry = create_adjustment(
            employee=self.employee,
            amount=Decimal("15"),
            notes="manual",
            user=self.mgmt,
        )
        self.client.login(username="mgmt", password="x")

        resp = self.client.post(
            reverse(
                "employees:employee_ledger_delete",
                args=[self.employee.pk, entry.pk],
            ),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(resp.status_code, 204)
        self.assertEqual(
            resp["HX-Redirect"],
            f"{reverse('employees:employee_detail', args=[self.employee.pk])}?tab=ledger",
        )
        self.assertFalse(EmployeeLedgerEntry.objects.filter(pk=entry.pk).exists())
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.current_balance, Decimal("0"))
