"""Pearl page kit coverage for customer management templates."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import UserProfile
from customers.services import create_customer, post_on_account_sale
from companies.models import Company, Product, ProductLine
from sales.models import PaymentMethod
from sales.services import create_sale

from customers.tests import CustomerListQueryCountTests  # noqa: F401 — keep prefetch in this suite

User = get_user_model()


def _decimal(v):
    return Decimal(str(v))


class CustomerListKitTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("custkit", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        cls.company = Company.objects.create(
            name="KitCo", opening_balance=_decimal(10000), current_balance=_decimal(10000)
        )
        line = ProductLine.objects.create(company=cls.company, name="L")
        cls.product = Product.objects.create(
            line=line,
            variant_label="P",
            cost_price=_decimal(5),
            default_sell_price=_decimal(200),
        )
        PaymentMethod.objects.create(name="Cash")

    def setUp(self):
        self.client.force_login(self.user)

    def test_customer_list_uses_kit_header(self):
        r = self.client.get(reverse("customers:customer_list"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, '<header class="rd-page-head">')
        self.assertContains(r, 'id="cust-q"')
        self.assertContains(r, 'name="q"')
        self.assertContains(r, "rd-filter")
        self.assertContains(r, reverse("customers:customers_export_csv"))
        self.assertContains(r, reverse("customers:customer_payments_export_csv"))

    def test_write_off_not_btn_warning(self):
        customer = create_customer(name="Debtor", user=self.user)
        sale = create_sale(
            company=self.company,
            product=self.product,
            reference_number="0599000333",
            payer_name=customer.name,
            payment_method=None,
            sell_price_actual=_decimal(100),
            notes="",
            user=self.user,
            on_account=True,
            customer=customer,
        )
        post_on_account_sale(sale=sale, user=self.user)
        customer.refresh_from_db()
        self.assertGreater(customer.current_balance, 0)

        r = self.client.get(reverse("customers:customer_list"))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        write_off_url = reverse("customers:customer_write_off", args=[customer.pk])
        self.assertIn(write_off_url, html)
        self.assertNotIn("btn-warning", html)
        self.assertIn("Write off", html)
