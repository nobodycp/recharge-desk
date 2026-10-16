from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import UserProfile
from companies.models import Company, Product, ProductLine
from customers.models import Customer, CustomerPaymentSubmission
from employees.models import EmployeeProfile
from sales.models import PaymentMethod, Sale


User = get_user_model()


class ManagementSalesKitTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = User.objects.create_user("sales_kit_manager", password="x")
        UserProfile.objects.update_or_create(
            user=cls.manager,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        company = Company.objects.create(name="Kit Company")
        line = ProductLine.objects.create(company=company, name="Kit Line")
        product = Product.objects.create(
            line=line,
            variant_label="Kit Product",
            cost_price=Decimal("5"),
            default_sell_price=Decimal("10"),
        )
        payment_method = PaymentMethod.objects.create(name="Kit Cash")
        cls.sale = Sale.objects.create(
            company=company,
            product=product,
            reference_number="0501234567",
            payer_name="Kit Payer",
            payment_method=payment_method,
            sell_price_actual=Decimal("10"),
            cost_price_snapshot=Decimal("5"),
            profit_snapshot=Decimal("5"),
            loss_snapshot=Decimal("0"),
            status=Sale.Status.PENDING,
            created_by=cls.manager,
        )

    def setUp(self):
        self.client.force_login(self.manager)

    def test_management_lists_render_page_kit_and_keep_filter_contracts(self):
        cases = (
            (
                "sales:management_sale_list",
                "sale-list-results",
                ("q", "company", "product", "employee", "payment_method", "status",
                 "esim", "entry_mode", "charge_status", "date_from", "date_to"),
            ),
            (
                "sales:pending_payments",
                "pending-payments-results",
                ("q", "company", "product", "employee", "payment_method", "esim",
                 "date_from", "date_to"),
            ),
            ("sales:awaiting_approvals", "awaiting-results", ("q",)),
        )

        for url_name, target_id, field_names in cases:
            with self.subTest(url_name=url_name):
                response = self.client.get(reverse(url_name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'class="rd-page"')
                self.assertContains(response, "rd-page-head")
                self.assertContains(response, "rd-filter-card")
                self.assertContains(response, f'id="{target_id}"')
                self.assertContains(response, f'hx-target="#{target_id}"')
                for field_name in field_names:
                    self.assertContains(response, f'name="{field_name}"')

    def test_management_exports_remain_unboosted(self):
        for url_name in (
            "sales:management_sale_list",
            "sales:pending_payments",
            "sales:awaiting_approvals",
        ):
            with self.subTest(url_name=url_name):
                response = self.client.get(reverse(url_name))
                self.assertContains(response, 'hx-boost="false"')
                self.assertContains(response, "Export CSV")

    def test_sale_edit_uses_form_page_and_kit_fields(self):
        response = self.client.get(reverse("sales:sale_edit", args=[self.sale.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "rd-page rd-page--form")
        self.assertContains(response, "rd-page-head")
        self.assertContains(response, "rd-form-grid")
        self.assertContains(response, "rd-form-actions")
        for field_name in (
            "payment_method",
            "payer_name",
            "reference_number",
            "sell_price_actual",
            "notes",
        ):
            self.assertContains(response, f'name="{field_name}"')


class EmployeeSalesKitTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.employee = User.objects.create_user("sales_kit_employee", password="x")
        UserProfile.objects.update_or_create(
            user=cls.employee,
            defaults={"role": UserProfile.Role.EMPLOYEE, "is_active_profile": True},
        )
        EmployeeProfile.objects.create(user=cls.employee)
        cls.company = Company.objects.create(name="Employee Kit Company")
        cls.line = ProductLine.objects.create(company=cls.company, name="Employee Kit Line")
        cls.product = Product.objects.create(
            line=cls.line,
            variant_label="Employee Kit Product",
            cost_price=Decimal("5"),
            default_sell_price=Decimal("10"),
        )
        cls.payment_method = PaymentMethod.objects.create(name="Employee Kit Cash")
        cls.customer = Customer.objects.create(
            name="Employee Kit Customer",
            created_by=cls.employee,
        )
        cls.sale = Sale.objects.create(
            company=cls.company,
            product=cls.product,
            reference_number="0501234567",
            payer_name="Employee Kit Payer",
            payment_method=cls.payment_method,
            sell_price_actual=Decimal("10"),
            cost_price_snapshot=Decimal("5"),
            profit_snapshot=Decimal("5"),
            loss_snapshot=Decimal("0"),
            status=Sale.Status.PENDING,
            created_by=cls.employee,
        )
        cls.payment = CustomerPaymentSubmission.objects.create(
            customer=cls.customer,
            amount=Decimal("20"),
            payment_method=cls.payment_method,
            created_by=cls.employee,
        )

    def setUp(self):
        self.client.force_login(self.employee)

    def test_employee_sales_pages_render_page_kit_headers(self):
        pages = (
            ("sales:employee_entry", ()),
            ("sales:employee_payment_edit", (self.payment.pk,)),
            ("sales:employee_recent_sales", ()),
            ("sales:employee_record_payment", ()),
            ("sales:employee_refresh_number", ()),
            ("sales:employee_sale_edit", (self.sale.pk,)),
            ("sales:employee_stock_check", ()),
        )

        for name, args in pages:
            with self.subTest(name=name):
                response = self.client.get(reverse(name, args=args))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'class="rd-page')
                html = response.content.decode()
                self.assertEqual(html.count("<h1"), 1)
                self.assertIn('<h1 class="rd-employee-title">', html)

    def test_entry_and_payment_forms_keep_post_names_and_actions(self):
        entry = self.client.get(reverse("sales:employee_entry"))
        for field_name in (
            "form_token",
            "reference_number",
            "payer_name",
            "payment_method",
            "on_account",
            "paid_via_employee",
            "company",
            "product",
            "duration_days",
            "sell_price_actual",
            "is_esim",
            "is_new_sim",
            "sim_serial_or_iccid",
            "notes",
        ):
            self.assertContains(entry, f'name="{field_name}"')
        self.assertContains(entry, 'name="action" value="save"')
        self.assertContains(entry, 'data-company-products="')
        self.assertNotContains(entry, 'hx-target="#product-tiles-container"')

        for name, args in (
            ("sales:employee_record_payment", ()),
            ("sales:employee_payment_edit", (self.payment.pk,)),
        ):
            with self.subTest(name=name):
                response = self.client.get(reverse(name, args=args))
                for field_name in (
                    "customer",
                    "amount",
                    "payment_method",
                    "paid_via_employee",
                    "notes",
                ):
                    self.assertContains(response, f'name="{field_name}"')

    def test_recent_filter_uses_kit_without_renaming_get_fields(self):
        response = self.client.get(reverse("sales:employee_recent_sales"))

        self.assertContains(response, "rd-filter-card")
        self.assertContains(response, "rd-filter-grid")
        for field_name in (
            "q",
            "company",
            "payment_method",
            "status",
            "date_from",
            "date_to",
        ):
            self.assertContains(response, f'name="{field_name}"')

    def test_stock_check_keeps_live_htmx_contract(self):
        response = self.client.get(reverse("sales:employee_stock_check"))

        self.assertContains(response, 'name="q"')
        self.assertContains(response, 'hx-target="#rd-stock-check-results"')
        self.assertContains(response, 'hx-select="#rd-stock-check-results"')
        self.assertContains(response, 'hx-swap="outerHTML"')
        self.assertContains(response, 'hx-push-url="true"')
