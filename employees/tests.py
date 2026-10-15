import re
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone, translation

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

    def test_delete_customer_payment_ledger_also_removes_customer_payment(self):
        from customers.models import CustomerLedger, CustomerPayment
        from customers.services import create_customer, record_customer_payment

        customer = create_customer(name="Sami", user=self.mgmt)
        payment = record_customer_payment(
            customer=customer,
            amount=Decimal("50"),
            payment_method=None,
            user=self.mgmt,
            paid_via_employee=True,
            employee_recipient=self.employee,
        )
        entry = EmployeeLedgerEntry.objects.get(
            reference_customer_payment=payment,
            entry_type=EmployeeLedgerEntry.EntryType.CUSTOMER_PAYMENT_RECEIVED,
        )
        self.employee.refresh_from_db()
        customer.refresh_from_db()
        self.assertEqual(self.employee.current_balance, Decimal("-50"))
        self.assertEqual(customer.current_balance, Decimal("-50"))

        delete_ledger_entry(entry=entry, user=self.mgmt)

        self.assertFalse(EmployeeLedgerEntry.objects.filter(pk=entry.pk).exists())
        self.assertFalse(CustomerPayment.objects.filter(pk=payment.pk).exists())
        self.assertFalse(
            CustomerLedger.objects.filter(
                customer=customer,
                entry_type=CustomerLedger.EntryType.PAYMENT,
            ).exists()
        )
        self.employee.refresh_from_db()
        customer.refresh_from_db()
        self.assertEqual(self.employee.current_balance, Decimal("0"))
        self.assertEqual(customer.current_balance, Decimal("0"))


class EmployeeMyBalanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.emp_user = User.objects.create_user(username="bal_cashier", password="x")
        UserProfile.objects.update_or_create(
            user=cls.emp_user,
            defaults={"role": UserProfile.Role.EMPLOYEE, "full_name": "Bal Cashier"},
        )
        cls.employee = EmployeeProfile.objects.create(
            user=cls.emp_user,
            monthly_salary=Decimal("2500"),
            current_balance=Decimal("1200"),
        )
        month = timezone.localdate().replace(day=1)
        EmployeeLedgerEntry.objects.create(
            employee=cls.employee,
            entry_type=EmployeeLedgerEntry.EntryType.SALARY_ACCRUAL,
            amount=Decimal("2500"),
            salary_month=month,
            notes="test accrual",
            created_by=cls.emp_user,
        )

    def test_my_balance_shows_salary_and_movements(self):
        entry = self.employee.ledger_entries.get()
        self.client.login(username="bal_cashier", password="x")
        resp = self.client.get(reverse("employees:my_balance"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Wallet details")
        self.assertEqual(resp.context["employee"].monthly_salary, Decimal("2500"))
        self.assertEqual(resp.context["employee"].current_balance, Decimal("1200"))
        self.assertContains(resp, "Movement statement")
        self.assertContains(resp, "rd-mobile-tabbar")
        self.assertContains(resp, "rd-employee-wallet")
        self.assertContains(resp, "test accrual")
        self.assertNotContains(resp, "Manual adjustment")
        self.assertNotContains(
            resp,
            reverse(
                "employees:employee_ledger_delete",
                args=[self.employee.pk, entry.pk],
            ),
        )

    def test_my_balance_empty_without_profile(self):
        lone = User.objects.create_user(username="no_profile_emp", password="x")
        UserProfile.objects.update_or_create(
            user=lone,
            defaults={"role": UserProfile.Role.EMPLOYEE, "full_name": "Lone"},
        )
        self.client.login(username="no_profile_emp", password="x")
        resp = self.client.get(reverse("employees:my_balance"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "No employee payroll profile is linked to your account yet.")


class EmployeePagesKitTests(TestCase):
    """Employees list, detail and form on the shared page kit."""

    @classmethod
    def setUpTestData(cls):
        cls.mgmt = User.objects.create_user(username="kit_mgmt", password="x")
        UserProfile.objects.update_or_create(
            user=cls.mgmt,
            defaults={"role": UserProfile.Role.MANAGEMENT, "full_name": "Kit Manager"},
        )
        cls.worker_user = User.objects.create_user(username="kit_worker", password="x")
        UserProfile.objects.update_or_create(
            user=cls.worker_user,
            defaults={"role": UserProfile.Role.EMPLOYEE, "full_name": "Kit Worker"},
        )
        cls.employee = EmployeeProfile.objects.create(
            user=cls.worker_user,
            monthly_salary=Decimal("3000"),
        )
        cls.free_user = User.objects.create_user(username="kit_free", password="x")
        UserProfile.objects.update_or_create(
            user=cls.free_user,
            defaults={"role": UserProfile.Role.EMPLOYEE, "full_name": "Kit Free"},
        )

    def setUp(self):
        self.client.login(username="kit_mgmt", password="x")
        self.detail_url = reverse("employees:employee_detail", args=[self.employee.pk])

    def _badge(self, html):
        match = re.search(r"data-rd-filter-count[^>]*>\s*(\d*)\s*<", html)
        self.assertIsNotNone(match, "filter badge missing")
        return match.group(1)

    def test_list_on_kit_with_accrual_label_and_confirm(self):
        resp = self.client.get(reverse("employees:employee_list"))
        self.assertContains(resp, 'class="rd-page"')
        self.assertContains(resp, "rd-page-head")
        self.assertContains(resp, reverse("employees:employee_create"))
        self.assertContains(resp, "rd-stat-strip")
        self.assertContains(resp, "Kit Worker")

    def test_accrual_has_confirm(self):
        resp = self.client.get(reverse("employees:employee_list"))
        html = resp.content.decode()
        form = re.search(
            r'<form[^>]*action="%s"[^>]*>' % re.escape(reverse("employees:employee_run_salary_accrual")),
            html,
        )
        self.assertIsNotNone(form)
        self.assertIn('hx-confirm="Run salary accrual for the selected month?"', form.group(0))
        self.assertIn('method="post"', form.group(0))
        self.assertContains(resp, 'for="salary-month"')
        self.assertContains(resp, 'name="salary_month"')
        self.assertContains(resp, "btn btn-outline-secondary btn-sm")
        self.assertNotContains(resp, "btn-outline-primary")

    def test_detail_on_kit(self):
        self.employee.current_balance = Decimal("120")
        self.employee.save()
        resp = self.client.get(self.detail_url)
        self.assertContains(resp, 'class="rd-page"')
        self.assertContains(resp, "rd-page-head")
        self.assertContains(resp, "@kit_worker")
        self.assertContains(resp, reverse("employees:employee_edit", args=[self.employee.pk]))
        self.assertContains(resp, "rd-stat-strip")
        self.assertContains(resp, "kpi-success")
        self.assertNotContains(resp, "text-success")
        self.assertContains(resp, "Current balance")
        self.assertContains(resp, "Monthly salary")
        self.assertContains(resp, "rd-section-tabs")
        # adjustment: help text is visible and Save is not full width.
        self.assertContains(resp, "Positive credits the employee; negative debits them.")
        adjustment = re.search(r'<form method="post" novalidate>.*?</form>', resp.content.decode(), re.S).group(0)
        self.assertIn("form_kind", adjustment)
        self.assertNotIn("w-100", adjustment)

    def test_detail_filter_badge_counts_only_filters(self):
        for tab in ("ledger", "payments"):
            resp = self.client.get(self.detail_url, {"tab": tab})
            self.assertEqual(self._badge(resp.content.decode()), "0", tab)
        resp = self.client.get(self.detail_url, {"tab": "ledger", "ledger_q": "bonus", "ledger_sort": "amount"})
        self.assertEqual(self._badge(resp.content.decode()), "1")
        resp = self.client.get(self.detail_url, {"tab": "payments", "payments_q": "x", "payments_sort": "payer"})
        self.assertEqual(self._badge(resp.content.decode()), "1")

    def test_detail_filter_keeps_tab_sort_and_targets_grid(self):
        resp = self.client.get(
            self.detail_url, {"tab": "ledger", "ledger_sort": "amount", "ledger_order": "asc"}
        )
        html = resp.content.decode()
        for name, value in (("tab", "ledger"), ("ledger_sort", "amount"), ("ledger_order", "asc")):
            self.assertRegex(html, r'<input type="hidden" name="%s" value="%s" data-rd-filter-keep>' % (name, value))
        self.assertContains(resp, 'hx-target="#employee-ledger-grid"')
        # The htmx submit must ask for the grid fragment, not the whole page.
        form_tag = re.search(r'<form[^>]*class="rd-filter-grid"[^>]*>', html).group(0)
        self.assertIn('hx-vals="{&quot;partial&quot;: &quot;ledger&quot;}"', form_tag)

    def test_reset_link_has_no_hx_vals_ancestor(self):
        for tab in ("ledger", "payments"):
            html = self.client.get(self.detail_url, {"tab": tab}).content.decode()
            before = html[: html.index('class="btn btn-outline-secondary btn-sm" href="%s?tab=%s"' % (self.detail_url, tab))]
            card = before[before.index("<details"):]
            # Nothing opened around the Reset link carries hx-vals except the <form> itself.
            self.assertEqual(card.count("hx-vals"), 1)
            form_open = card[card.rindex("<form"):]
            self.assertIn("hx-vals", form_open.split(">", 1)[0])
            wrapper = before[: before.index("<details")]
            self.assertNotRegex(wrapper[-200:], r"<div[^>]*hx-vals")

    def test_range_error_rendered_in_htmx_grid_partial(self):
        text = "&#x27;Date from&#x27; must be on or before &#x27;Date to&#x27;."
        for tab in ("ledger", "payments"):
            params = {
                "partial": tab,
                "tab": tab,
                f"{tab}_date_from": "2026-02-10",
                f"{tab}_date_to": "2026-02-01",
            }
            resp = self.client.get(self.detail_url, params, HTTP_HX_REQUEST="true")
            self.assertContains(resp, text)
            self.assertContains(resp, 'class="rd-field__error rd-grid-error" role="alert"')
            ok = self.client.get(self.detail_url, {"partial": tab, "tab": tab}, HTTP_HX_REQUEST="true")
            self.assertNotContains(ok, "rd-grid-error")

    def test_range_error_shown_once_on_full_page(self):
        text = "&#x27;Date from&#x27; must be on or before &#x27;Date to&#x27;."
        resp = self.client.get(
            self.detail_url,
            {"tab": "ledger", "ledger_date_from": "2026-02-10", "ledger_date_to": "2026-02-01"},
        )
        self.assertContains(resp, text, count=1)
        self.assertNotContains(resp, "rd-grid-error")

    def test_adjustment_placeholder_translated_to_arabic(self):
        self.addCleanup(translation.activate, "en")
        self.client.cookies["django_language"] = "ar"
        resp = self.client.get(self.detail_url, follow=True)
        self.assertContains(resp, 'placeholder="ملاحظات اختيارية"')

    def test_detail_date_range_error_visible(self):
        reversed_range = {"ledger_date_from": "2026-02-10", "ledger_date_to": "2026-02-01"}
        resp = self.client.get(self.detail_url, {"tab": "ledger", **reversed_range})
        self.assertContains(resp, "&#x27;Date from&#x27; must be on or before &#x27;Date to&#x27;.")
        self.assertContains(resp, 'role="alert"')
        reversed_range = {"payments_date_from": "2026-02-10", "payments_date_to": "2026-02-01"}
        resp = self.client.get(self.detail_url, {"tab": "payments", **reversed_range})
        self.assertContains(resp, "&#x27;Date from&#x27; must be on or before &#x27;Date to&#x27;.")

    def test_detail_invalid_range_still_drops_filters(self):
        create_adjustment(employee=self.employee, amount=Decimal("10"), notes="keep-me", user=self.mgmt)
        resp = self.client.get(
            self.detail_url,
            {"tab": "ledger", "ledger_q": "nomatch", "ledger_date_from": "2026-02-10", "ledger_date_to": "2026-02-01"},
        )
        self.assertContains(resp, "keep-me")

    def test_detail_partials_render_for_htmx(self):
        create_adjustment(employee=self.employee, amount=Decimal("10"), notes="ledger-note", user=self.mgmt)
        EmployeeLedgerEntry.objects.create(
            employee=self.employee,
            entry_type=EmployeeLedgerEntry.EntryType.SALES_PAYMENT_RECEIVED,
            amount=Decimal("-20"),
            phone="0555",
            payer_name="Pay Er",
            created_by=self.mgmt,
        )
        ledger = self.client.get(self.detail_url, {"partial": "ledger", "tab": "ledger"}, HTTP_HX_REQUEST="true")
        self.assertEqual(ledger.status_code, 200)
        self.assertTemplateUsed(ledger, "employees/partials/employee_ledger_results.html")
        self.assertTemplateNotUsed(ledger, "employees/employee_detail.html")
        self.assertContains(ledger, "ledger-note")
        self.assertContains(ledger, "rd-datagrid-sort")
        self.assertContains(ledger, "ledger_sort=amount")
        self.assertContains(ledger, "ledger_page=1")
        self.assertNotContains(ledger, "<html")

        payments = self.client.get(self.detail_url, {"partial": "payments", "tab": "payments"}, HTTP_HX_REQUEST="true")
        self.assertEqual(payments.status_code, 200)
        self.assertTemplateUsed(payments, "employees/partials/employee_payments_results.html")
        self.assertTemplateNotUsed(payments, "employees/employee_detail.html")
        self.assertContains(payments, "Pay Er")
        self.assertContains(payments, "payments_sort=payer")
        self.assertNotContains(payments, "<html")

    def test_detail_sort_links_keep_tab_and_reset_page(self):
        resp = self.client.get(
            self.detail_url,
            {"tab": "payments", "payments_page": "2", "payments_sort": "payer", "payments_order": "desc"},
        )
        html = resp.content.decode()
        self.assertRegex(html, r'class="rd-datagrid-sort is-active is-desc" href="[^"]*payments_order=asc[^"]*"')
        for href in re.findall(r'class="rd-datagrid-sort[^"]*" href="([^"]*)"', html):
            self.assertIn("payments_page=1", href)
            self.assertIn("tab=payments", href)

    def test_payments_pagination_uses_payments_page_key(self):
        for i in range(30):
            EmployeeLedgerEntry.objects.create(
                employee=self.employee,
                entry_type=EmployeeLedgerEntry.EntryType.SALES_PAYMENT_RECEIVED,
                amount=Decimal("-1"),
                phone=f"05{i:08d}",
                created_by=self.mgmt,
            )
        resp = self.client.get(self.detail_url, {"tab": "payments", "payments_page": "2"})
        self.assertContains(resp, "payments_page=1")
        self.assertNotContains(resp, "?page=")
        self.assertNotContains(resp, "&amp;page=")

    def test_ledger_delete_uses_row_actions_with_confirm(self):
        entry = create_adjustment(employee=self.employee, amount=Decimal("15"), notes="manual", user=self.mgmt)
        resp = self.client.get(self.detail_url, {"tab": "ledger"})
        delete_url = reverse("employees:employee_ledger_delete", args=[self.employee.pk, entry.pk])
        html = resp.content.decode()
        self.assertEqual(html.count('hx-post="%s"' % delete_url), 2)  # desktop row + mobile card
        self.assertContains(resp, "rd-icon-btn rd-icon-btn--danger")
        self.assertContains(resp, 'hx-target="body"')
        self.assertContains(resp, 'hx-swap="outerHTML"')
        self.assertContains(
            resp,
            'hx-confirm="Delete this employee ledger entry? The employee balance will be corrected. This cannot be undone."',
            count=2,
        )

    def test_empty_states_use_kit_rows(self):
        resp = self.client.get(self.detail_url, {"tab": "ledger"})
        self.assertContains(resp, "rd-empty-row")
        self.assertContains(resp, "No ledger entries.")
        resp = self.client.get(self.detail_url, {"tab": "payments"})
        self.assertContains(resp, "rd-empty-row")
        self.assertContains(resp, "No sales payments yet.")

    def test_adjustment_post_still_works(self):
        resp = self.client.post(
            self.detail_url, {"form_kind": "adjustment", "amount": "25", "notes": "kit adj"}
        )
        self.assertEqual(resp.status_code, 302)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.current_balance, Decimal("25"))
        bad = self.client.post(self.detail_url, {"form_kind": "adjustment", "amount": "0"})
        self.assertContains(bad, "Amount cannot be zero.")
        self.assertContains(bad, "is-invalid")

    def test_employee_form_on_kit(self):
        resp = self.client.get(reverse("employees:employee_create"))
        self.assertContains(resp, "rd-page rd-page--form")
        self.assertContains(resp, "rd-page-head")
        self.assertContains(resp, "rd-form-actions")
        self.assertContains(resp, "rd-form-grid")
        self.assertContains(resp, ">Choose…<")
        self.assertNotContains(resp, "---------")
        self.assertContains(resp, 'href="%s"' % reverse("employees:employee_list"), count=3)  # back, cancel, sidebar

    def test_employee_form_edit_backs_to_detail_and_shows_errors(self):
        url = reverse("employees:employee_edit", args=[self.employee.pk])
        resp = self.client.get(url)
        self.assertContains(resp, "rd-page--form")
        self.assertContains(resp, 'href="%s"' % self.detail_url)
        self.assertContains(resp, 'name="is_active"')
        bad = self.client.post(url, {"user": self.worker_user.pk, "monthly_salary": "-5"})
        self.assertContains(bad, "is-invalid")
        self.assertContains(bad, 'aria-invalid="true"')
        self.assertContains(bad, 'value="-5"')

    def test_new_strings_translated_to_arabic(self):
        self.addCleanup(translation.activate, "en")  # the request leaves "ar" active in this thread
        self.client.cookies["django_language"] = "ar"
        resp = self.client.get(reverse("employees:employee_list"), follow=True)
        self.assertContains(resp, 'hx-confirm="تشغيل استحقاق الرواتب للشهر المحدد؟"')
        resp = self.client.get(reverse("employees:employee_create"), follow=True)
        self.assertContains(resp, ">اختر…<")
