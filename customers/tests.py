from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Sum
from django.test import TestCase

from companies.models import Company, Product, ProductLine
from customers.models import Customer, CustomerLedger, CustomerPayment
from customers.services import (
    approve_sale,
    create_customer,
    delete_ledger_entry,
    record_customer_payment,
    reject_sale,
)
from sales.models import PaymentMethod, Sale
from sales.query_utils import confirmed_sales
from sales.services import cancel_sale, create_sale, delete_sale_permanently

User = get_user_model()


def _decimal(v):
    return Decimal(str(v))


class CustomerARTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        from accounts.models import UserProfile

        cls.user = User.objects.create_user("mgr", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        cls.company = Company.objects.create(
            name="Co", opening_balance=_decimal(10000), current_balance=_decimal(10000)
        )
        cls.line = ProductLine.objects.create(company=cls.company, name="L")
        cls.product = Product.objects.create(
            line=cls.line,
            variant_label="P",
            cost_price=_decimal(5),
            default_sell_price=_decimal(200),
        )
        cls.cash = PaymentMethod.objects.create(name="Cash")
        cls.bank = PaymentMethod.objects.create(name="Bank of Palestine")

    def _new_customer(self, name="Hazem"):
        return create_customer(name=name, user=self.user)

    def _new_on_account_sale(self, customer, sell):
        return create_sale(
            company=self.company,
            product=self.product,
            reference_number=f"059-{sell}",
            payer_name=customer.name,
            payment_method=None,
            sell_price_actual=_decimal(sell),
            notes="",
            user=self.user,
            on_account=True,
            customer=customer,
        )


class CreateOnAccountSaleTests(CustomerARTestCase):
    def test_on_account_sale_starts_awaiting(self):
        c = self._new_customer()
        s = self._new_on_account_sale(c, 200)
        self.assertEqual(s.status, Sale.Status.AWAITING)
        self.assertTrue(s.on_account)
        self.assertIsNone(s.payment_method)
        self.assertEqual(s.customer_id, c.pk)

    def test_on_account_requires_customer(self):
        with self.assertRaises(ValueError):
            create_sale(
                company=self.company,
                product=self.product,
                reference_number="x",
                payer_name="x",
                payment_method=None,
                sell_price_actual=_decimal(10),
                notes="",
                user=self.user,
                on_account=True,
                customer=None,
            )

    def test_non_account_requires_payment_method(self):
        with self.assertRaises(ValueError):
            create_sale(
                company=self.company,
                product=self.product,
                reference_number="x",
                payer_name="x",
                payment_method=None,
                sell_price_actual=_decimal(10),
                notes="",
                user=self.user,
                on_account=False,
                customer=None,
            )

    def test_awaiting_sales_excluded_from_aggregates(self):
        c = self._new_customer()
        self._new_on_account_sale(c, 200)
        # A normal paid sale to compare against.
        create_sale(
            company=self.company,
            product=self.product,
            reference_number="ok",
            payer_name="X",
            payment_method=self.cash,
            sell_price_actual=_decimal(50),
            notes="",
            user=self.user,
        )
        confirmed = confirmed_sales(Sale.objects.all())
        self.assertEqual(confirmed.count(), 1)
        self.assertEqual(
            confirmed.aggregate(s=Sum("sell_price_actual"))["s"], _decimal(50)
        )


class ApproveRejectTests(CustomerARTestCase):
    def test_approve_creates_charge_and_increments_balance(self):
        c = self._new_customer()
        s = self._new_on_account_sale(c, 200)
        approve_sale(sale=s, user=self.user)
        s.refresh_from_db()
        c.refresh_from_db()
        self.assertEqual(s.status, Sale.Status.PENDING)
        self.assertEqual(c.current_balance, _decimal(200))
        self.assertEqual(
            CustomerLedger.objects.filter(
                customer=c, entry_type=CustomerLedger.EntryType.CHARGE
            ).count(),
            1,
        )

    def test_approve_rejects_non_awaiting(self):
        c = self._new_customer()
        s = self._new_on_account_sale(c, 200)
        approve_sale(sale=s, user=self.user)
        s.refresh_from_db()
        with self.assertRaises(ValueError):
            approve_sale(sale=s, user=self.user)

    def test_reject_cancels_and_does_not_charge(self):
        c = self._new_customer()
        s = self._new_on_account_sale(c, 200)
        reject_sale(sale=s, user=self.user)
        s.refresh_from_db()
        c.refresh_from_db()
        self.assertEqual(s.status, Sale.Status.CANCELLED)
        self.assertEqual(c.current_balance, _decimal(0))
        self.assertEqual(CustomerLedger.objects.filter(customer=c).count(), 0)


class RecordPaymentFifoTests(CustomerARTestCase):
    def test_partial_payment_settles_oldest_charges(self):
        c = self._new_customer()
        s1 = self._new_on_account_sale(c, 200)
        s2 = self._new_on_account_sale(c, 200)
        s3 = self._new_on_account_sale(c, 300)
        for s in [s1, s2, s3]:
            approve_sale(sale=s, user=self.user)
        c.refresh_from_db()
        self.assertEqual(c.current_balance, _decimal(700))

        record_customer_payment(
            customer=c,
            amount=_decimal(500),
            payment_method=self.bank,
            user=self.user,
        )
        c.refresh_from_db()
        s1.refresh_from_db(); s2.refresh_from_db(); s3.refresh_from_db()
        self.assertEqual(c.current_balance, _decimal(200))
        self.assertEqual(s1.status, Sale.Status.PAID)
        self.assertEqual(s2.status, Sale.Status.PAID)
        self.assertEqual(s3.status, Sale.Status.PENDING)
        self.assertEqual(s1.payment_method_id, self.bank.pk)
        self.assertEqual(s2.payment_method_id, self.bank.pk)
        self.assertIsNotNone(s1.customer_payment_id)

    def test_overpayment_creates_credit_consumed_by_next_charge(self):
        c = self._new_customer()
        s1 = self._new_on_account_sale(c, 300)
        s2 = self._new_on_account_sale(c, 300)
        for s in [s1, s2]:
            approve_sale(sale=s, user=self.user)
        c.refresh_from_db()
        self.assertEqual(c.current_balance, _decimal(600))

        record_customer_payment(
            customer=c,
            amount=_decimal(1000),
            payment_method=self.bank,
            user=self.user,
        )
        c.refresh_from_db()
        s1.refresh_from_db(); s2.refresh_from_db()
        self.assertEqual(s1.status, Sale.Status.PAID)
        self.assertEqual(s2.status, Sale.Status.PAID)
        self.assertEqual(c.current_balance, _decimal(-400))

        # Next charge of 500 against -400 credit: credit is consumed but the
        # sale stays PENDING because FIFO settles whole sales only.
        s3 = self._new_on_account_sale(c, 500)
        approve_sale(sale=s3, user=self.user)
        c.refresh_from_db()
        s3.refresh_from_db()
        self.assertEqual(s3.status, Sale.Status.PENDING)
        self.assertEqual(c.current_balance, _decimal(100))

        # A smaller next charge fits inside the remaining credit and auto-settles.
        s4 = self._new_on_account_sale(c, 50)
        # Bring the balance back to credit by making a payment that fully clears s3.
        record_customer_payment(
            customer=c, amount=_decimal(150), payment_method=self.bank, user=self.user,
        )
        approve_sale(sale=s4, user=self.user)
        c.refresh_from_db()
        s3.refresh_from_db(); s4.refresh_from_db()
        self.assertEqual(s3.status, Sale.Status.PAID)
        self.assertEqual(s4.status, Sale.Status.PAID)

    def test_payment_must_be_positive(self):
        c = self._new_customer()
        with self.assertRaises(ValueError):
            record_customer_payment(
                customer=c,
                amount=_decimal(0),
                payment_method=self.bank,
                user=self.user,
            )


class CancelSettledSaleTests(CustomerARTestCase):
    def test_cancelling_settled_sale_reverses_customer_ledger(self):
        c = self._new_customer()
        s1 = self._new_on_account_sale(c, 200)
        approve_sale(sale=s1, user=self.user)
        record_customer_payment(
            customer=c,
            amount=_decimal(200),
            payment_method=self.bank,
            user=self.user,
        )
        s1.refresh_from_db()
        c.refresh_from_db()
        self.assertEqual(s1.status, Sale.Status.PAID)
        self.assertEqual(c.current_balance, _decimal(0))

        cancel_sale(sale=s1, user=self.user)
        s1.refresh_from_db()
        c.refresh_from_db()
        self.assertEqual(s1.status, Sale.Status.CANCELLED)
        # Customer was charged 200, paid 200; cancelling refunds the 200 charge,
        # so the customer should now hold 200 in credit.
        self.assertEqual(c.current_balance, _decimal(-200))
        self.assertTrue(
            CustomerLedger.objects.filter(
                customer=c,
                entry_type=CustomerLedger.EntryType.REVERSAL,
                sale=s1,
            ).exists()
        )

    def test_rejecting_awaiting_does_not_touch_customer_ledger(self):
        c = self._new_customer()
        s1 = self._new_on_account_sale(c, 200)
        reject_sale(sale=s1, user=self.user)
        c.refresh_from_db()
        self.assertEqual(c.current_balance, _decimal(0))
        self.assertEqual(CustomerLedger.objects.filter(customer=c).count(), 0)


class DeleteApprovedOnAccountSaleTests(CustomerARTestCase):
    def test_delete_approved_pending_sale_clears_customer_charge(self):
        c = self._new_customer()
        s = self._new_on_account_sale(c, 200)
        approve_sale(sale=s, user=self.user)
        c.refresh_from_db()
        self.assertEqual(c.current_balance, _decimal(200))

        delete_sale_permanently(sale=s)
        c.refresh_from_db()
        self.assertEqual(c.current_balance, _decimal(0))
        self.assertFalse(CustomerLedger.objects.filter(customer=c).exists())
        self.assertFalse(Sale.objects.filter(pk=s.pk).exists())

    def test_delete_awaiting_sale_does_not_touch_customer(self):
        c = self._new_customer()
        s = self._new_on_account_sale(c, 200)
        delete_sale_permanently(sale=s)
        c.refresh_from_db()
        self.assertEqual(c.current_balance, _decimal(0))


class DeleteLedgerEntryTests(CustomerARTestCase):
    def test_delete_orphan_charge_row_clears_balance(self):
        """Manual cleanup path for stray CHARGE rows whose sale is gone."""
        c = self._new_customer()
        s = self._new_on_account_sale(c, 200)
        approve_sale(sale=s, user=self.user)
        Sale.objects.filter(pk=s.pk).delete()

        c.refresh_from_db()
        self.assertEqual(c.current_balance, _decimal(200))

        orphan = CustomerLedger.objects.get(customer=c, entry_type=CustomerLedger.EntryType.CHARGE)
        delete_ledger_entry(entry=orphan, user=self.user)

        c.refresh_from_db()
        self.assertEqual(c.current_balance, _decimal(0))
        self.assertFalse(CustomerLedger.objects.filter(customer=c).exists())

    def test_payment_row_with_intact_payment_is_protected(self):
        c = self._new_customer()
        record_customer_payment(
            customer=c, amount=_decimal(100), payment_method=self.bank, user=self.user,
        )
        row = CustomerLedger.objects.get(customer=c, entry_type=CustomerLedger.EntryType.PAYMENT)
        with self.assertRaises(ValueError):
            delete_ledger_entry(entry=row, user=self.user)


class CustomerDetailViewTests(CustomerARTestCase):
    def test_detail_renders_with_on_account_history(self):
        """Regression: counts must use a fresh queryset, not the sliced list."""
        c = self._new_customer()
        s1 = self._new_on_account_sale(c, 200)
        s2 = self._new_on_account_sale(c, 300)
        approve_sale(sale=s1, user=self.user)
        self.client.force_login(self.user)
        url = f"/management/customers/{c.pk}/"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, c.name)
