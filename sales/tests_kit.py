from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import UserProfile
from companies.models import Company, Product, ProductLine
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
