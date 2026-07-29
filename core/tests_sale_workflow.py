"""Auto-approval workflow tests driven by AppSettings."""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import UserProfile
from companies.models import Company, Product, ProductLine
from core.models import AppSettings
from core.sale_workflow import finalize_payment_submission_after_entry, finalize_sale_after_entry
from customers.models import Customer, CustomerPaymentSubmission
from customers.services import submit_customer_payment_submission
from sales.models import PaymentMethod, Sale
from sales.services import create_sale

User = get_user_model()


class SaleWorkflowSettingsTests(TestCase):
    _DEFAULT_WORKFLOW = {
        "require_debt_request_approval": True,
        "require_settlement_request_approval": True,
        "require_payment_request_approval": True,
    }

    def tearDown(self):
        AppSettings.objects.update_or_create(pk=1, defaults=self._DEFAULT_WORKFLOW)

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("wf", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        cls.company = Company.objects.create(name="Co", opening_balance=Decimal("1000"))
        line = ProductLine.objects.create(company=cls.company, name="Line")
        cls.product = Product.objects.create(
            line=line,
            cost_price=Decimal("10"),
            default_sell_price=Decimal("20"),
        )
        cls.pm = PaymentMethod.objects.create(name="Cash")
        cls.customer = Customer.objects.create(name="Ali", created_by=cls.user)

    def _cash_sale(self):
        return create_sale(
            company=self.company,
            product=self.product,
            reference_number="0501111111",
            payer_name="Walk-in",
            payment_method=self.pm,
            sell_price_actual=Decimal("20"),
            notes="",
            user=self.user,
        )

    def _on_account_sale(self):
        return create_sale(
            company=self.company,
            product=self.product,
            reference_number="0502222222",
            payer_name=self.customer.name,
            payment_method=None,
            sell_price_actual=Decimal("30"),
            notes="",
            user=self.user,
            on_account=True,
            customer=self.customer,
        )

    def test_cash_auto_paid_when_payment_approval_off(self):
        AppSettings.objects.update_or_create(
            pk=1,
            defaults={"require_payment_request_approval": False},
        )
        sale = self._cash_sale()
        outcome = finalize_sale_after_entry(sale=sale, user=self.user, on_account=False)
        sale.refresh_from_db()
        self.assertEqual(outcome, "paid")
        self.assertEqual(sale.status, Sale.Status.PAID)

    def test_on_account_auto_posted_when_debt_approval_off(self):
        AppSettings.objects.update_or_create(
            pk=1,
            defaults={"require_debt_request_approval": False},
        )
        sale = self._on_account_sale()
        outcome = finalize_sale_after_entry(sale=sale, user=self.user, on_account=True)
        sale.refresh_from_db()
        self.customer.refresh_from_db()
        self.assertEqual(outcome, "posted_debt")
        self.assertEqual(sale.status, Sale.Status.PENDING)
        self.assertEqual(self.customer.current_balance, Decimal("30"))

    def test_employee_payment_auto_paid_even_when_approval_required(self):
        from employees.models import EmployeeProfile
        from core.sale_workflow import finalize_sale_after_entry

        AppSettings.objects.update_or_create(
            pk=1,
            defaults={"require_payment_request_approval": True},
        )
        emp_user = User.objects.create_user(username="cashier2", password="x")
        UserProfile.objects.update_or_create(
            user=emp_user,
            defaults={"role": UserProfile.Role.EMPLOYEE, "is_active_profile": True},
        )
        employee = EmployeeProfile.objects.create(
            user=emp_user,
            monthly_salary=Decimal("0"),
        )
        sale = create_sale(
            company=self.company,
            product=self.product,
            reference_number="0503333333",
            payer_name="Walk-in",
            payment_method=None,
            sell_price_actual=Decimal("20"),
            notes="",
            user=emp_user,
            paid_via_employee=True,
            employee_recipient=employee,
        )
        outcome = finalize_sale_after_entry(
            sale=sale, user=emp_user, on_account=False, paid_via_employee=True
        )
        sale.refresh_from_db()
        self.assertEqual(outcome, "paid_employee")
        self.assertEqual(sale.status, Sale.Status.PAID)

    def test_settlement_auto_applied_when_approval_off(self):
        AppSettings.objects.update_or_create(
            pk=1,
            defaults={"require_settlement_request_approval": False},
        )
        sub = submit_customer_payment_submission(
            customer=self.customer,
            amount=Decimal("15"),
            payment_method=self.pm,
            notes="",
            user=self.user,
        )
        applied = finalize_payment_submission_after_entry(submission=sub, user=self.user)
        sub.refresh_from_db()
        self.customer.refresh_from_db()
        self.assertTrue(applied)
        self.assertEqual(sub.status, CustomerPaymentSubmission.Status.APPROVED)
        self.assertEqual(self.customer.current_balance, Decimal("-15"))

    def test_employee_held_settlement_skips_approval_queue(self):
        from employees.models import EmployeeLedgerEntry, EmployeeProfile

        AppSettings.objects.update_or_create(
            pk=1,
            defaults={"require_settlement_request_approval": True},
        )
        emp_user = User.objects.create_user(username="anas", password="x")
        UserProfile.objects.update_or_create(
            user=emp_user,
            defaults={"role": UserProfile.Role.EMPLOYEE, "is_active_profile": True},
        )
        employee = EmployeeProfile.objects.create(user=emp_user, monthly_salary=Decimal("0"))
        sub = submit_customer_payment_submission(
            customer=self.customer,
            amount=Decimal("50"),
            payment_method=None,
            notes="",
            user=emp_user,
            paid_via_employee=True,
            employee_recipient=employee,
        )
        applied = finalize_payment_submission_after_entry(submission=sub, user=emp_user)
        sub.refresh_from_db()
        self.customer.refresh_from_db()
        employee.refresh_from_db()
        self.assertTrue(applied)
        self.assertEqual(sub.status, CustomerPaymentSubmission.Status.APPROVED)
        self.assertEqual(self.customer.current_balance, Decimal("-50"))
        self.assertEqual(employee.current_balance, Decimal("-50"))
        self.assertTrue(
            EmployeeLedgerEntry.objects.filter(
                employee=employee,
                entry_type=EmployeeLedgerEntry.EntryType.CUSTOMER_PAYMENT_RECEIVED,
                amount=Decimal("-50"),
            ).exists()
        )