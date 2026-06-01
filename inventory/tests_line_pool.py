from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import UserProfile
from companies.models import Company, Product, ProductLine
from inventory.models import SimStockBalance
from inventory.forms import ReceiveMainStockForm
from inventory.services import allocate_to_customer, receive_main_stock
from sales.models import PaymentMethod
from sales.services import create_sale, mark_sale_paid
from customers.models import Customer

User = get_user_model()


class SharedSimLinePoolTests(TestCase):
    """Sleekom under Sky and Areen shares one inventory pool."""

    @classmethod
    def setUpTestData(cls):
        cls.sky = Company.objects.create(name="Sky", current_balance=Decimal("100"))
        cls.areen = Company.objects.create(name="Areen", current_balance=Decimal("100"))
        cls.line_sky = ProductLine.objects.create(company=cls.sky, name="Sleekom")
        cls.line_areen = ProductLine.objects.create(company=cls.areen, name="Sleekom")
        cls.product_sky = Product.objects.create(
            line=cls.line_sky,
            variant_label="",
            cost_price=Decimal("5"),
            default_sell_price=Decimal("10"),
        )
        cls.product_areen = Product.objects.create(
            line=cls.line_areen,
            variant_label="",
            cost_price=Decimal("5"),
            default_sell_price=Decimal("10"),
        )
        cls.pm = PaymentMethod.objects.create(name="Cash")
        cls.mgmt = User.objects.create_user("mgr_pool", password="x")
        UserProfile.objects.update_or_create(
            user=cls.mgmt,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        cls.emp = User.objects.create_user("emp_pool", password="x")
        UserProfile.objects.update_or_create(
            user=cls.emp,
            defaults={"role": UserProfile.Role.EMPLOYEE, "is_active_profile": True},
        )

    def test_receive_on_one_line_visible_for_same_name(self):
        receive_main_stock(product_line=self.line_sky, qty=7, notes="", user=self.mgmt)
        main = SimStockBalance.objects.get(
            location=SimStockBalance.Location.MAIN,
            product_line__name__iexact="Sleekom",
            customer=None,
        )
        self.assertEqual(main.quantity, 7)
        self.assertEqual(SimStockBalance.objects.filter(location="main").count(), 1)

    def test_receive_form_lists_one_shared_line_per_name(self):
        form = ReceiveMainStockForm()
        labels = [form.fields["product_line"].label_from_instance(line) for line in form.fields["product_line"].queryset]

        self.assertEqual(labels.count("Sleekom"), 1)
        self.assertFalse(any("Sky" in label or "Areen" in label for label in labels))

    def test_allocate_missing_serial_raises_validation_error(self):
        receive_main_stock(product_line=self.line_sky, qty=1, notes="", user=self.mgmt)
        customer = Customer.objects.create(name="Dealer Missing Serial", created_by=self.mgmt)

        with self.assertRaisesMessage(ValueError, "not registered in main stock"):
            allocate_to_customer(
                customer=customer,
                product_line=self.line_sky,
                qty=1,
                notes="",
                user=self.mgmt,
                serials=["missing-serial"],
            )

    def test_sale_under_areen_deducts_shared_sleekom_pool(self):
        receive_main_stock(product_line=self.line_sky, qty=3, notes="", user=self.mgmt)
        sale = create_sale(
            company=self.areen,
            product=self.product_areen,
            reference_number="0590000099",
            payer_name="Walk-in",
            payment_method=self.pm,
            sell_price_actual=Decimal("10"),
            notes="",
            user=self.emp,
            is_new_sim=True,
        )
        mark_sale_paid(sale=sale, user=self.mgmt)
        main = SimStockBalance.objects.get(
            location=SimStockBalance.Location.MAIN,
            product_line__name__iexact="Sleekom",
            customer=None,
        )
        self.assertEqual(main.quantity, 2)
