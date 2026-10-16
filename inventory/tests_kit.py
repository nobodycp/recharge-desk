from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import UserProfile
from companies.models import Company, ProductLine
from customers.models import Customer


User = get_user_model()


class InventoryPageKitTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(
            name="Kit Company",
            current_balance=Decimal("0"),
        )
        cls.line = ProductLine.objects.create(company=cls.company, name="Kit Line")
        cls.management = User.objects.create_user("inventory_kit_manager", password="x")
        UserProfile.objects.update_or_create(
            user=cls.management,
            defaults={
                "role": UserProfile.Role.MANAGEMENT,
                "is_active_profile": True,
            },
        )
        cls.employee = User.objects.create_user("inventory_kit_employee", password="x")
        UserProfile.objects.update_or_create(
            user=cls.employee,
            defaults={
                "role": UserProfile.Role.EMPLOYEE,
                "is_active_profile": True,
            },
        )
        cls.customer = Customer.objects.create(
            name="Kit Customer",
            created_by=cls.management,
        )

    def test_management_inventory_pages_render_the_page_kit_and_navigation(self):
        self.client.force_login(self.management)
        pages = (
            ("inventory:overview", ()),
            ("inventory:main", ()),
            ("inventory:movements", ()),
            ("inventory:cards", ()),
            ("inventory:line_detail", (self.line.pk,)),
            ("inventory:customers", ()),
            ("inventory:customer_detail", (self.customer.pk,)),
        )

        for name, args in pages:
            with self.subTest(name=name):
                response = self.client.get(reverse(name, args=args))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'class="rd-page"')
                self.assertContains(response, 'class="rd-page-head"')
                self.assertContains(response, "rd-section-tabs")

    def test_employee_inventory_pages_keep_read_only_routes_on_the_page_kit(self):
        self.client.force_login(self.employee)
        pages = (
            ("inventory:employee_overview", ()),
            ("inventory:employee_main", ()),
            ("inventory:employee_movements", ()),
            ("inventory:employee_cards", ()),
            ("inventory:employee_line_detail", (self.line.pk,)),
            ("inventory:employee_customers", ()),
            ("inventory:employee_customer_detail", (self.customer.pk,)),
        )

        for name, args in pages:
            with self.subTest(name=name):
                response = self.client.get(reverse(name, args=args))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'class="rd-page"')
                self.assertContains(response, 'class="rd-page-head"')
                self.assertContains(response, "rd-section-tabs")

    def test_inventory_filters_use_the_kit_without_renaming_get_fields(self):
        self.client.force_login(self.management)

        movements = self.client.get(reverse("inventory:movements"))
        self.assertContains(movements, 'class="rd-filter-grid')
        for name in ("movement_type", "product_line", "customer", "date_from", "date_to"):
            self.assertContains(movements, f'name="{name}"')
        self.assertContains(
            movements,
            "Type part of the name — finds any matching customer.",
        )

        cards = self.client.get(reverse("inventory:cards"))
        self.assertContains(cards, 'class="rd-filter-grid')
        self.assertContains(cards, 'name="q"')

        customers = self.client.get(reverse("inventory:customers"))
        self.assertContains(customers, 'id="rd-customer-stock-filter"')
        self.assertContains(customers, 'name="q"')
        self.assertContains(customers, 'id="id_inv_customer_q"')
        self.assertContains(customers, 'name="product_line"')
