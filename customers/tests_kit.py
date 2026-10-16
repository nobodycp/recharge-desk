"""Pearl page kit coverage for customer management templates."""

from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from accounts.models import UserProfile
from customers.models import Customer, CustomerPhone
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


class CustomerFormKitTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("custformkit", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_customer_create_uses_kit_and_saves(self):
        r = self.client.get(reverse("customers:customer_create"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "rd-page rd-page--form")
        self.assertContains(r, '<header class="rd-page-head">')
        self.assertContains(r, "rd-field")
        self.assertContains(r, "rd-form-actions")
        self.assertNotContains(r, "max-width")

        r = self.client.post(
            reverse("customers:customer_create"),
            {
                "name": "Kit Customer",
                "notes": "Created via kit",
                "is_active": "on",
                "initial_phone": "0599888777",
            },
        )
        self.assertEqual(r.status_code, 302)
        customer = Customer.objects.get(name="Kit Customer")
        self.assertEqual(customer.notes, "Created via kit")
        self.assertTrue(customer.is_active)
        self.assertTrue(
            CustomerPhone.objects.filter(customer=customer, phone="0599888777").exists()
        )

    def test_customer_edit_uses_kit_and_saves(self):
        customer = create_customer(name="Before Edit", notes="old", user=self.user)
        r = self.client.get(reverse("customers:customer_edit", args=[customer.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "rd-page--form")
        self.assertContains(r, "rd-field")

        r = self.client.post(
            reverse("customers:customer_edit", args=[customer.pk]),
            {
                "name": "After Edit",
                "notes": "updated notes",
                "is_active": "",
                "initial_phone": "",
            },
        )
        self.assertEqual(r.status_code, 302)
        customer.refresh_from_db()
        self.assertEqual(customer.name, "After Edit")
        self.assertEqual(customer.notes, "updated notes")
        self.assertFalse(customer.is_active)

    def test_customer_create_invalid_shows_field_errors(self):
        r = self.client.post(
            reverse("customers:customer_create"),
            {"name": "", "notes": "", "initial_phone": ""},
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "rd-field__error")


class CustomerDetailKitTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("custdetailkit", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        cls.company = Company.objects.create(
            name="DetailCo", opening_balance=_decimal(10000), current_balance=_decimal(10000)
        )
        line = ProductLine.objects.create(company=cls.company, name="Detail line")
        cls.product = Product.objects.create(
            line=line,
            variant_label="Detail product",
            cost_price=_decimal(5),
            default_sell_price=_decimal(200),
        )
        PaymentMethod.objects.create(name="Detail cash")
        cls.customer = create_customer(name="Detail Customer", user=cls.user)

    def setUp(self):
        self.client.force_login(self.user)

    def test_customer_detail_uses_kit_header_and_fields(self):
        r = self.client.get(reverse("customers:customer_detail", args=[self.customer.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, '<div class="rd-page">')
        self.assertContains(r, '<header class="rd-page-head">')
        self.assertContains(r, reverse("customers:customer_statement", args=[self.customer.pk]))
        self.assertContains(
            r, reverse("customers:customer_detailed_invoice", args=[self.customer.pk])
        )
        self.assertContains(r, reverse("customers:customer_list"))
        self.assertContains(r, ">Back</a>")
        self.assertContains(r, "rd-stat-strip")
        self.assertContains(r, "rd-field")
        self.assertContains(r, "rd-table-modern")

    def test_customer_sale_row_keeps_htmx_delete_contract(self):
        sale = create_sale(
            company=self.company,
            product=self.product,
            reference_number="0599000444",
            payer_name=self.customer.name,
            payment_method=None,
            sell_price_actual=_decimal(100),
            notes="",
            user=self.user,
            on_account=True,
            customer=self.customer,
        )
        post_on_account_sale(sale=sale, user=self.user)

        r = self.client.get(reverse("customers:customer_detail", args=[self.customer.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, f'id="customer-sale-row-{sale.pk}"')
        self.assertContains(r, f'hx-target="#customer-sale-row-{sale.pk}"')
        self.assertContains(r, 'hx-swap="outerHTML swap:200ms"')
        self.assertContains(r, 'hx-confirm="')

    def test_inactive_customer_is_neutral_not_warning(self):
        self.customer.is_active = False
        self.customer.save(update_fields=["is_active"])

        r = self.client.get(reverse("customers:customer_detail", args=[self.customer.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Inactive")
        self.assertContains(r, "rd-badge--neutral")
        self.assertNotContains(r, "text-warning")


class CustomerDocumentsKitTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("custdocumentskit", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        cls.customer = create_customer(name="Documents Customer", user=cls.user)

    def setUp(self):
        self.client.force_login(self.user)

    def test_customer_statement_uses_kit_header_without_table_light(self):
        r = self.client.get(reverse("customers:customer_statement", args=[self.customer.pk]))

        self.assertEqual(r.status_code, 200)
        self.assertContains(r, '<header class="rd-page-head">')
        self.assertContains(r, "rd-table-modern")
        self.assertNotContains(r, "table-light")

    def test_customer_detailed_invoice_uses_kit_header_without_table_light(self):
        r = self.client.get(
            reverse("customers:customer_detailed_invoice", args=[self.customer.pk])
        )

        self.assertEqual(r.status_code, 200)
        self.assertContains(r, '<header class="rd-page-head">')
        self.assertContains(r, "rd-table-modern")
        self.assertNotContains(r, "table-light")

    def test_document_header_period_stays_translated_in_arabic(self):
        with translation.override("ar"):
            self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "ar"
            urls = (
                reverse("customers:customer_statement", args=[self.customer.pk]),
                reverse("customers:customer_detailed_invoice", args=[self.customer.pk]),
            )
            for url in urls:
                with self.subTest(url=url):
                    r = self.client.get(
                        url,
                        {"period_from": "2026-10-01", "period_to": "2026-10-16"},
                        HTTP_ACCEPT_LANGUAGE="ar",
                    )

                    self.assertEqual(r.status_code, 200)
                    self.assertContains(r, self.customer.name)
                    self.assertContains(r, " · من ")
                    self.assertNotContains(r, " · From ")
