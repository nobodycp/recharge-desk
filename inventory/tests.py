from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from accounts.models import UserProfile
from companies.models import Company, Product, ProductLine
from customers.models import Customer
from customers.services import approve_customer_payment_submission, approve_sale
from customers.models import CustomerPaymentSubmission
from inventory.models import SimCard, SimStockBalance, SimStockMovement
from inventory.services import (
    AMBIGUOUS,
    allocate_to_customer,
    consume_sim_for_sale,
    receive_main_stock,
    resolve_sim_customer,
)
from sales.models import PaymentMethod, Sale
from sales.services import cancel_sale, create_sale, mark_sale_paid

User = get_user_model()


class SimInventoryTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(
            name="Co",
            opening_balance=Decimal("1000"),
            current_balance=Decimal("1000"),
        )
        cls.line = ProductLine.objects.create(company=cls.company, name="Hot")
        cls.product = Product.objects.create(
            line=cls.line,
            variant_label="P",
            cost_price=Decimal("5"),
            default_sell_price=Decimal("10"),
        )
        cls.pm = PaymentMethod.objects.create(name="Cash")
        cls.mgmt = User.objects.create_user("mgmt_sim", password="x")
        UserProfile.objects.update_or_create(
            user=cls.mgmt,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        cls.emp = User.objects.create_user("emp_sim", password="x")
        UserProfile.objects.update_or_create(
            user=cls.emp,
            defaults={"role": UserProfile.Role.EMPLOYEE, "is_active_profile": True},
        )


class SimStockServiceTests(SimInventoryTestBase):
    def test_receive_and_allocate(self):
        receive_main_stock(product_line=self.line, qty=10, notes="", user=self.mgmt)
        main = SimStockBalance.objects.get(
            location=SimStockBalance.Location.MAIN, product_line=self.line, customer=None
        )
        self.assertEqual(main.quantity, 10)
        customer = Customer.objects.create(name="Dealer A", created_by=self.mgmt)
        allocate_to_customer(
            customer=customer, product_line=self.line, qty=4, notes="", user=self.mgmt
        )
        main.refresh_from_db()
        cust = SimStockBalance.objects.get(
            location=SimStockBalance.Location.CUSTOMER,
            product_line=self.line,
            customer=customer,
        )
        self.assertEqual(main.quantity, 6)
        self.assertEqual(cust.quantity, 4)
        self.assertEqual(
            SimStockMovement.objects.filter(
                movement_type=SimStockMovement.MovementType.ALLOCATE_TO_CUSTOMER
            ).count(),
            1,
        )

    def test_allocate_rejects_insufficient_main(self):
        customer = Customer.objects.create(name="Dealer B", created_by=self.mgmt)
        with self.assertRaises(ValueError):
            allocate_to_customer(
                customer=customer, product_line=self.line, qty=1, notes="", user=self.mgmt
            )

    def test_receive_with_serial_registers_card(self):
        receive_main_stock(
            product_line=self.line,
            qty=1,
            notes="",
            user=self.mgmt,
            serials=["8901234567890"],
        )
        card = SimCard.objects.get(serial_or_iccid="8901234567890")
        self.assertEqual(card.status, SimCard.Status.IN_MAIN)
        self.assertEqual(card.product_line_id, self.line.pk)


class SimConsumeTests(SimInventoryTestBase):
    def _sale(self, *, payer_name="Walk-in", is_new_sim=True, on_account=False, customer=None):
        return create_sale(
            company=self.company,
            product=self.product,
            reference_number="0590000001",
            payer_name=payer_name,
            payment_method=None if on_account else self.pm,
            sell_price_actual=Decimal("10"),
            notes="",
            user=self.emp,
            is_new_sim=is_new_sim,
            on_account=on_account,
            customer=customer,
        )

    def test_consume_from_main_when_no_customer_match(self):
        receive_main_stock(product_line=self.line, qty=2, notes="", user=self.mgmt)
        sale = self._sale(payer_name="Unknown Person")
        mark_sale_paid(sale=sale, user=self.mgmt)
        sale.refresh_from_db()
        self.assertIsNotNone(sale.sim_consumed_at)
        self.assertEqual(sale.sim_deducted_from, Sale.SimDeductedFrom.MAIN)
        main = SimStockBalance.objects.get(
            location=SimStockBalance.Location.MAIN, product_line=self.line, customer=None
        )
        self.assertEqual(main.quantity, 1)

    def test_consume_from_customer_when_match_and_stock(self):
        customer = Customer.objects.create(name="Dealer C", created_by=self.mgmt)
        receive_main_stock(product_line=self.line, qty=5, notes="", user=self.mgmt)
        allocate_to_customer(
            customer=customer, product_line=self.line, qty=2, notes="", user=self.mgmt
        )
        sale = self._sale(payer_name="Dealer C")
        mark_sale_paid(sale=sale, user=self.mgmt)
        sale.refresh_from_db()
        self.assertEqual(sale.sim_deducted_from, Sale.SimDeductedFrom.CUSTOMER)
        cust = SimStockBalance.objects.get(
            location=SimStockBalance.Location.CUSTOMER,
            product_line=self.line,
            customer=customer,
        )
        self.assertEqual(cust.quantity, 1)

    def test_consume_rejects_customer_without_line_stock(self):
        customer = Customer.objects.create(name="Dealer D", created_by=self.mgmt)
        receive_main_stock(product_line=self.line, qty=1, notes="", user=self.mgmt)
        sale = self._sale(payer_name="Dealer D")
        with self.assertRaises(ValueError):
            mark_sale_paid(sale=sale, user=self.mgmt)

    def test_consume_rejects_ambiguous_payer_name(self):
        Customer.objects.create(name="Dup Name", created_by=self.mgmt)
        Customer.objects.create(name="dup name", created_by=self.mgmt)
        self.assertEqual(resolve_sim_customer("Dup Name"), AMBIGUOUS)
        receive_main_stock(product_line=self.line, qty=1, notes="", user=self.mgmt)
        sale = self._sale(payer_name="Dup Name")
        with self.assertRaises(ValueError):
            mark_sale_paid(sale=sale, user=self.mgmt)

    def test_consume_on_approve_sale(self):
        customer = Customer.objects.create(name="Credit Co", created_by=self.mgmt)
        receive_main_stock(product_line=self.line, qty=3, notes="", user=self.mgmt)
        allocate_to_customer(
            customer=customer, product_line=self.line, qty=1, notes="", user=self.mgmt
        )
        sale = self._sale(
            payer_name="Credit Co",
            on_account=True,
            customer=customer,
        )
        approve_sale(sale=sale, user=self.mgmt)
        sale.refresh_from_db()
        self.assertIsNotNone(sale.sim_consumed_at)

    def test_consume_idempotent(self):
        receive_main_stock(product_line=self.line, qty=2, notes="", user=self.mgmt)
        sale = self._sale()
        mark_sale_paid(sale=sale, user=self.mgmt)
        consume_sim_for_sale(sale=sale, user=self.mgmt)
        self.assertEqual(
            SimStockMovement.objects.filter(
                movement_type=SimStockMovement.MovementType.SALE_CONSUME, sale=sale
            ).count(),
            1,
        )

    def test_reversal_on_cancel(self):
        receive_main_stock(product_line=self.line, qty=2, notes="", user=self.mgmt)
        sale = self._sale()
        mark_sale_paid(sale=sale, user=self.mgmt)
        cancel_sale(sale=sale, user=self.mgmt)
        sale.refresh_from_db()
        self.assertIsNone(sale.sim_consumed_at)
        main = SimStockBalance.objects.get(
            location=SimStockBalance.Location.MAIN, product_line=self.line, customer=None
        )
        self.assertEqual(main.quantity, 2)

    def test_payment_submission_fifo_consumes_oldest_new_sim(self):
        customer = Customer.objects.create(name="Fifo Client", created_by=self.mgmt)
        receive_main_stock(product_line=self.line, qty=5, notes="", user=self.mgmt)
        allocate_to_customer(
            customer=customer, product_line=self.line, qty=3, notes="", user=self.mgmt
        )
        older = self._sale(
            payer_name="Fifo Client",
            on_account=True,
            customer=customer,
        )
        newer = self._sale(
            payer_name="Fifo Client",
            on_account=True,
            customer=customer,
        )
        approve_sale(sale=older, user=self.mgmt)
        older.refresh_from_db()
        newer.refresh_from_db()
        self.assertIsNotNone(older.sim_consumed_at)
        self.assertIsNone(newer.sim_consumed_at)

        sub = CustomerPaymentSubmission.objects.create(
            customer=customer,
            amount=Decimal("5"),
            payment_method=self.pm,
            created_by=self.emp,
            status=CustomerPaymentSubmission.Status.AWAITING,
        )
        approve_customer_payment_submission(submission=sub, user=self.mgmt)
        newer.refresh_from_db()
        self.assertIsNotNone(newer.sim_consumed_at)
