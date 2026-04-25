from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Sum
from django.test import TestCase

from companies.models import Company, Product, ProductLine
from customers.models import Customer, CustomerLedger, CustomerPayment, CustomerPaymentSubmission
from customers.services import (
    approve_customer_payment_submission,
    approve_sale,
    create_customer,
    delete_customer_completely,
    delete_customer_payment,
    delete_ledger_entry,
    record_customer_adjustment,
    record_customer_payment,
    reject_customer_payment_submission,
    reject_sale,
    submit_customer_payment_submission,
    write_off_customer_balance,
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


class CreateCustomerPhoneLinkTests(CustomerARTestCase):
    def test_same_phone_can_be_linked_to_multiple_customers(self):
        """Regression: get_or_create must filter by (customer, phone), not phone
        alone, otherwise the second customer silently ends up with no phone."""
        from customers.models import CustomerPhone

        a = create_customer(name="Hazem", phones=["0599000111"], user=self.user)
        b = create_customer(name="Mahmoud", phones=["0599000111"], user=self.user)

        self.assertTrue(
            CustomerPhone.objects.filter(customer=a, phone="0599000111").exists()
        )
        self.assertTrue(
            CustomerPhone.objects.filter(customer=b, phone="0599000111").exists()
        )
        self.assertEqual(CustomerPhone.objects.filter(phone="0599000111").count(), 2)

    def test_create_customer_is_idempotent_on_duplicate_phone_for_same_customer(self):
        from customers.models import CustomerPhone

        c = create_customer(
            name="Hazem", phones=["0599000111", "0599000111"], user=self.user
        )
        self.assertEqual(
            CustomerPhone.objects.filter(customer=c, phone="0599000111").count(), 1
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


class DeleteCustomerPaymentTests(CustomerARTestCase):
    def test_delete_payment_reverts_settlement_and_balance(self):
        c = self._new_customer()
        s = self._new_on_account_sale(c, 90)
        approve_sale(sale=s, user=self.user)
        record_customer_payment(
            customer=c, amount=_decimal(90), payment_method=self.bank, user=self.user,
        )

        s.refresh_from_db()
        c.refresh_from_db()
        self.assertEqual(s.status, Sale.Status.PAID)
        self.assertEqual(c.current_balance, _decimal(0))

        payment = CustomerPayment.objects.get(customer=c)
        delete_customer_payment(payment=payment, user=self.user)

        s.refresh_from_db()
        c.refresh_from_db()
        self.assertEqual(s.status, Sale.Status.PENDING)
        self.assertIsNone(s.payment_method)
        self.assertIsNone(s.customer_payment_id)
        self.assertEqual(c.current_balance, _decimal(90))
        self.assertFalse(CustomerPayment.objects.filter(pk=payment.pk).exists())
        self.assertEqual(CustomerLedger.objects.filter(customer=c).count(), 1)


class WriteOffCustomerBalanceTests(CustomerARTestCase):
    def test_write_off_marks_sales_records_loss_clears_balance(self):
        from sales.query_utils import (
            confirmed_sales,
            loss_eligible_sales,
            paid_sales_only,
        )
        from django.db.models import Sum

        c = self._new_customer()
        s1 = self._new_on_account_sale(c, 100)
        s2 = self._new_on_account_sale(c, 50)
        approve_sale(sale=s1, user=self.user)
        approve_sale(sale=s2, user=self.user)

        c.refresh_from_db()
        self.assertEqual(c.current_balance, _decimal(150))

        result = write_off_customer_balance(customer=c, user=self.user)

        self.assertEqual(result["sales_written_off"], 2)
        self.assertEqual(result["debt_cleared"], _decimal(150))
        # cost_price is 5 per sale (from base setUp).
        self.assertEqual(result["loss_total"], _decimal(10))

        s1.refresh_from_db(); s2.refresh_from_db(); c.refresh_from_db()
        self.assertEqual(s1.status, Sale.Status.WRITTEN_OFF)
        self.assertEqual(s2.status, Sale.Status.WRITTEN_OFF)
        self.assertEqual(s1.loss_snapshot, _decimal(5))
        self.assertEqual(s2.loss_snapshot, _decimal(5))
        self.assertEqual(c.current_balance, _decimal(0))
        self.assertFalse(c.is_active)

        # WRITTEN_OFF must drop out of volume / profit aggregates.
        all_sales = Sale.objects.all()
        self.assertEqual(
            confirmed_sales(all_sales).aggregate(s=Sum("sell_price_actual"))["s"] or 0,
            _decimal(0),
        )
        self.assertEqual(
            paid_sales_only(all_sales).aggregate(s=Sum("profit_snapshot"))["s"] or 0,
            _decimal(0),
        )
        # ...but their cost should now show under losses.
        self.assertEqual(
            loss_eligible_sales(all_sales).aggregate(s=Sum("loss_snapshot"))["s"],
            _decimal(10),
        )


class CustomerAdjustmentTests(CustomerARTestCase):
    def test_positive_adjustment_increases_debt_and_does_not_touch_reports(self):
        from sales.query_utils import (
            confirmed_sales,
            loss_eligible_sales,
            paid_sales_only,
        )

        c = self._new_customer()
        record_customer_adjustment(
            customer=c, amount=_decimal(80), notes="legacy", user=self.user
        )
        c.refresh_from_db()
        self.assertEqual(c.current_balance, _decimal(80))
        entry = CustomerLedger.objects.get(customer=c)
        self.assertEqual(entry.entry_type, CustomerLedger.EntryType.ADJUSTMENT)
        self.assertEqual(entry.amount, _decimal(80))

        all_sales = Sale.objects.all()
        self.assertEqual(
            confirmed_sales(all_sales).aggregate(s=Sum("sell_price_actual"))["s"] or 0,
            _decimal(0),
        )
        self.assertEqual(
            paid_sales_only(all_sales).aggregate(s=Sum("profit_snapshot"))["s"] or 0,
            _decimal(0),
        )
        self.assertEqual(
            loss_eligible_sales(all_sales).aggregate(s=Sum("loss_snapshot"))["s"] or 0,
            _decimal(0),
        )

    def test_negative_adjustment_credits_balance(self):
        c = self._new_customer()
        record_customer_adjustment(
            customer=c, amount=_decimal(-30), user=self.user
        )
        c.refresh_from_db()
        self.assertEqual(c.current_balance, _decimal(-30))

    def test_zero_amount_rejected(self):
        c = self._new_customer()
        with self.assertRaises(ValueError):
            record_customer_adjustment(customer=c, amount=_decimal(0), user=self.user)

    def test_payment_settles_adjustment_via_balance(self):
        c = self._new_customer()
        record_customer_adjustment(customer=c, amount=_decimal(120), user=self.user)
        record_customer_payment(
            customer=c, amount=_decimal(120), payment_method=self.bank, user=self.user
        )
        c.refresh_from_db()
        self.assertEqual(c.current_balance, _decimal(0))


class DeleteCustomerCompletelyTests(CustomerARTestCase):
    def test_deletes_customer_with_sales_payments_phones(self):
        c = self._new_customer()
        c.phones.create(phone="0599000111")
        s1 = self._new_on_account_sale(c, 100)
        s2 = self._new_on_account_sale(c, 50)
        approve_sale(sale=s1, user=self.user)
        approve_sale(sale=s2, user=self.user)
        record_customer_payment(
            customer=c, amount=_decimal(100), payment_method=self.bank, user=self.user,
        )

        from customers.models import CustomerPhone

        delete_customer_completely(customer=c, user=self.user)

        self.assertFalse(Customer.objects.filter(pk=c.pk).exists())
        self.assertFalse(Sale.objects.filter(pk__in=[s1.pk, s2.pk]).exists())
        self.assertEqual(CustomerLedger.objects.filter(customer_id=c.pk).count(), 0)
        self.assertEqual(CustomerPayment.objects.filter(customer_id=c.pk).count(), 0)
        self.assertEqual(CustomerPhone.objects.filter(customer_id=c.pk).count(), 0)


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


class CustomerStatementTests(CustomerARTestCase):
    """End-to-end tests for the per-customer date-ranged statement page."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def _url(self, c, **params):
        from urllib.parse import urlencode

        base = f"/management/customers/{c.pk}/statement/"
        return f"{base}?{urlencode(params)}" if params else base

    def test_default_period_is_current_month_and_renders(self):
        c = self._new_customer()
        s = self._new_on_account_sale(c, 300)
        approve_sale(sale=s, user=self.user)
        record_customer_payment(
            customer=c, amount=_decimal(100), payment_method=self.cash, user=self.user
        )
        r = self.client.get(self._url(c))
        self.assertEqual(r.status_code, 200)
        ctx = r.context
        self.assertEqual(ctx["charges_total"], _decimal(300))
        self.assertEqual(ctx["payments_total"], _decimal(100))
        # opening = 0 (no activity before today), closing = +300 - 100 = 200
        self.assertEqual(ctx["opening_balance"], _decimal(0))
        self.assertEqual(ctx["closing_balance"], _decimal(200))
        # running balance on the last entry equals closing balance
        self.assertEqual(ctx["ledger_chrono"][-1].running_balance, _decimal(200))

    def test_period_excludes_out_of_range_activity(self):
        from datetime import timedelta

        from django.utils import timezone

        c = self._new_customer()
        # Old sale: dragged 60 days into the past.
        s_old = self._new_on_account_sale(c, 500)
        approve_sale(sale=s_old, user=self.user)
        old_dt = timezone.now() - timedelta(days=60)
        Sale.objects.filter(pk=s_old.pk).update(created_at=old_dt)
        CustomerLedger.objects.filter(sale=s_old).update(created_at=old_dt)

        # New sale today.
        s_new = self._new_on_account_sale(c, 200)
        approve_sale(sale=s_new, user=self.user)

        today = timezone.localdate()
        r = self.client.get(
            self._url(c, period_from=today.isoformat(), period_to=today.isoformat())
        )
        self.assertEqual(r.status_code, 200)
        ctx = r.context
        self.assertEqual(ctx["charges_total"], _decimal(200))  # only new sale
        # opening balance = balance from the old approved sale
        self.assertEqual(ctx["opening_balance"], _decimal(500))
        # closing = opening + new charge = 700
        self.assertEqual(ctx["closing_balance"], _decimal(700))

    def test_csv_download_returns_bom_and_lines(self):
        c = self._new_customer()
        s = self._new_on_account_sale(c, 300)
        approve_sale(sale=s, user=self.user)
        record_customer_payment(
            customer=c, amount=_decimal(100), payment_method=self.cash, user=self.user
        )
        r = self.client.get(f"/management/customers/{c.pk}/statement.csv")
        self.assertEqual(r.status_code, 200)
        body = b"".join(r.streaming_content)
        self.assertTrue(body.startswith(b"\xef\xbb\xbf"), "CSV must include UTF-8 BOM")
        text = body.decode("utf-8-sig")
        self.assertIn("300", text)
        self.assertIn("100", text)


class CustomerListQueryCountTests(CustomerARTestCase):
    """Regression: customer_list.html iterates `c.phones.all|slice:":3"`
    on every row. Without prefetch_related the page issued one phone
    query per customer, so a 30-customer page = ~31 queries."""

    def test_phones_are_prefetched_on_list_page(self):
        for i in range(8):
            cust = create_customer(name=f"C{i}", phones=[f"05{i:08d}"], user=self.user)
            cust.phones.create(phone=f"06{i:08d}")
            cust.phones.create(phone=f"07{i:08d}")
        self.client.force_login(self.user)

        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get("/management/customers/")
        self.assertEqual(resp.status_code, 200)
        # The list itself + the prefetch + a couple of aggregates +
        # auth/session lookups ~ <= 15 queries even with 8 customers.
        # If prefetch_related is removed this jumps to ~25+.
        self.assertLess(
            len(ctx.captured_queries),
            20,
            f"customer_list issued {len(ctx.captured_queries)} queries; phones prefetch likely regressed.",
        )


class CustomerPaymentSubmissionFlowTests(CustomerARTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from accounts.models import UserProfile

        cls.emp = User.objects.create_user("emp_pay_sub", password="x")
        UserProfile.objects.update_or_create(
            user=cls.emp,
            defaults={"role": UserProfile.Role.EMPLOYEE, "is_active_profile": True},
        )

    def test_submit_does_not_change_balance_approve_does(self):
        c = self._new_customer()
        approve_sale(sale=self._new_on_account_sale(c, 5000), user=self.user)
        c.refresh_from_db()
        self.assertEqual(c.current_balance, _decimal(5000))

        sub = submit_customer_payment_submission(
            customer=c,
            amount=_decimal(3000),
            payment_method=self.bank,
            notes="bp",
            user=self.emp,
        )
        self.assertEqual(sub.status, CustomerPaymentSubmission.Status.AWAITING)
        c.refresh_from_db()
        self.assertEqual(c.current_balance, _decimal(5000))

        approve_customer_payment_submission(submission=sub, user=self.user)
        c.refresh_from_db()
        self.assertEqual(c.current_balance, _decimal(2000))
        sub.refresh_from_db()
        self.assertEqual(sub.status, CustomerPaymentSubmission.Status.APPROVED)
        self.assertTrue(
            CustomerPayment.objects.filter(customer=c, amount=_decimal(3000)).exists()
        )

    def test_overpayment_approval_makes_negative_balance(self):
        c = self._new_customer()
        approve_sale(sale=self._new_on_account_sale(c, 100), user=self.user)
        sub = submit_customer_payment_submission(
            customer=c,
            amount=_decimal(250),
            payment_method=self.cash,
            notes="",
            user=self.emp,
        )
        approve_customer_payment_submission(submission=sub, user=self.user)
        c.refresh_from_db()
        self.assertEqual(c.current_balance, _decimal(-150))

    def test_reject_leaves_balance(self):
        c = self._new_customer()
        approve_sale(sale=self._new_on_account_sale(c, 400), user=self.user)
        sub = submit_customer_payment_submission(
            customer=c,
            amount=_decimal(100),
            payment_method=self.cash,
            notes="",
            user=self.emp,
        )
        reject_customer_payment_submission(submission=sub, user=self.user, reason="no")
        c.refresh_from_db()
        self.assertEqual(c.current_balance, _decimal(400))
        sub.refresh_from_db()
        self.assertEqual(sub.status, CustomerPaymentSubmission.Status.REJECTED)

    def test_employee_http_post_creates_submission(self):
        from django.urls import reverse

        c = self._new_customer()
        approve_sale(sale=self._new_on_account_sale(c, 80), user=self.user)
        self.client.force_login(self.emp)
        r = self.client.post(
            reverse("sales:employee_submit_customer_payment_submission"),
            {
                "customer": str(c.pk),
                "amount": "25.00",
                "payment_method": str(self.cash.pk),
                "notes": "test",
            },
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(
            CustomerPaymentSubmission.objects.filter(
                customer=c, status=CustomerPaymentSubmission.Status.AWAITING
            ).count(),
            1,
        )
