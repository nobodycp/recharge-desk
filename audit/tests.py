"""Tests for the audit log: events get recorded for the right service
calls, the viewer is management-only, and filters narrow correctly.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import UserProfile
from audit.models import AuditAction, AuditLog
from companies.models import Company, Product, ProductLine
from customers.models import Customer
from customers.services import (
    create_customer,
    record_customer_adjustment,
    record_customer_payment,
)
from sales.models import PaymentMethod, Sale
from sales.services import (
    cancel_sale,
    create_sale,
    delete_sale_permanently,
    mark_sale_paid,
    update_sale_fields,
)

User = get_user_model()


class AuditRecordingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("audit_emp", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        cls.co = Company.objects.create(
            name="Co",
            opening_balance=Decimal("1000"),
            current_balance=Decimal("1000"),
        )
        cls.line = ProductLine.objects.create(company=cls.co, name="L")
        cls.prod = Product.objects.create(
            line=cls.line,
            variant_label="P",
            cost_price=Decimal("5"),
            default_sell_price=Decimal("10"),
        )
        cls.pm = PaymentMethod.objects.create(name="Cash")

    def _make_sale(self):
        return create_sale(
            company=self.co,
            product=self.prod,
            reference_number="0590000001",
            payer_name="Test",
            payment_method=self.pm,
            sell_price_actual=Decimal("10"),
            notes="",
            user=self.user,
        )

    def test_create_sale_writes_audit_row(self):
        s = self._make_sale()
        rows = AuditLog.objects.filter(model_label="sales.sale", object_id=str(s.pk))
        self.assertEqual(rows.count(), 1)
        row = rows.first()
        self.assertEqual(row.action, AuditAction.CREATE)
        self.assertEqual(row.actor, self.user)
        self.assertIn("reference_number", row.changes)

    def test_update_sale_records_diff(self):
        s = self._make_sale()
        update_sale_fields(
            sale=s,
            payment_method=self.pm,
            payer_name="Other",
            reference_number=s.reference_number,
            sell_price_actual=Decimal("12"),
            notes="updated",
            user=self.user,
        )
        row = AuditLog.objects.filter(action=AuditAction.UPDATE, object_id=str(s.pk)).first()
        self.assertIsNotNone(row)
        self.assertIn("payer_name", row.changes)
        self.assertEqual(row.changes["payer_name"]["old"], "Test")
        self.assertEqual(row.changes["payer_name"]["new"], "Other")

    def test_mark_paid_writes_audit_row(self):
        s = self._make_sale()
        mark_sale_paid(sale=s, user=self.user)
        self.assertTrue(
            AuditLog.objects.filter(action=AuditAction.MARK_PAID, object_id=str(s.pk)).exists()
        )

    def test_cancel_sale_writes_audit_row(self):
        s = self._make_sale()
        cancel_sale(sale=s, user=self.user)
        self.assertTrue(
            AuditLog.objects.filter(action=AuditAction.CANCEL, object_id=str(s.pk)).exists()
        )

    def test_delete_sale_writes_audit_row_with_snapshot(self):
        s = self._make_sale()
        sid = s.pk
        delete_sale_permanently(sale=s, user=self.user)
        row = AuditLog.objects.filter(action=AuditAction.DELETE, object_id=str(sid)).first()
        self.assertIsNotNone(row)
        self.assertIn("_snapshot", row.changes)
        self.assertIn("reference_number", row.changes["_snapshot"])

    def test_customer_payment_and_adjustment_recorded(self):
        cust = create_customer(name="Hazem", user=self.user)
        record_customer_payment(
            customer=cust,
            amount=Decimal("50"),
            payment_method=self.pm,
            user=self.user,
        )
        record_customer_adjustment(
            customer=cust,
            amount=Decimal("20"),
            user=self.user,
        )
        self.assertTrue(AuditLog.objects.filter(action=AuditAction.PAY).exists())
        self.assertTrue(AuditLog.objects.filter(action=AuditAction.ADJUST).exists())
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditAction.CREATE, model_label="customers.customer"
            ).exists()
        )


class AuditLogViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.boss = User.objects.create_user("audit_boss", password="x")
        UserProfile.objects.update_or_create(
            user=cls.boss,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        cls.worker = User.objects.create_user("audit_worker", password="x")
        UserProfile.objects.update_or_create(
            user=cls.worker,
            defaults={"role": UserProfile.Role.EMPLOYEE, "is_active_profile": True},
        )
        # Seed two events to filter on.
        cust = Customer.objects.create(name="X", created_by=cls.boss)
        AuditLog.objects.create(
            actor=cls.boss,
            action=AuditAction.CREATE,
            model_label="customers.customer",
            object_id=str(cust.pk),
            object_repr="X",
        )
        AuditLog.objects.create(
            actor=cls.boss,
            action=AuditAction.UPDATE,
            model_label="sales.sale",
            object_id="42",
            object_repr="dummy",
        )

    def test_employee_blocked(self):
        c = Client()
        c.force_login(self.worker)
        r = c.get(reverse("audit:audit_log_list"))
        self.assertEqual(r.status_code, 302)

    def test_management_can_view_log(self):
        c = Client()
        c.force_login(self.boss)
        r = c.get(reverse("audit:audit_log_list"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "customers.customer")
        self.assertContains(r, "sales.sale")

    def test_action_filter_narrows_results(self):
        c = Client()
        c.force_login(self.boss)
        r = c.get(reverse("audit:audit_log_list"), {"action": AuditAction.UPDATE})
        # Page list is the only place where the *row* content (the
        # object id) shows up. The filter dropdown still lists every
        # model so we can't test against the bare label string.
        self.assertEqual(len(r.context["page_obj"].object_list), 1)
        row = r.context["page_obj"].object_list[0]
        self.assertEqual(row.action, AuditAction.UPDATE)
        self.assertEqual(row.model_label, "sales.sale")
