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

    def test_audit_filter_form_keeps_get_names(self):
        import re

        c = Client()
        c.force_login(self.boss)
        r = c.get(reverse("audit:audit_log_list"))
        html = r.content.decode()
        form_html = html[html.index('class="rd-filter-grid"'):]
        form_html = form_html[: form_html.index("</form>")]
        names = re.findall(r'<(?:input|select)\b[^>]*\bname="([^"]+)"', form_html)
        self.assertEqual(names, ["q", "action", "model", "actor", "date_from", "date_to"])
        self.assertContains(r, "rd-page-head")

    def test_audit_filter_form_preserves_values(self):
        c = Client()
        c.force_login(self.boss)
        r = c.get(
            reverse("audit:audit_log_list"),
            {"action": "update", "model": "sales.sale", "date_from": "2026-01-02", "q": "dum"},
        )
        self.assertContains(r, 'value="2026-01-02"')
        self.assertContains(r, 'value="dum"')
        self.assertContains(r, '<option value="update" selected>')
        self.assertContains(r, '<option value="sales.sale" selected>')
        self.assertEqual(len(r.context["page_obj"].object_list), 1)

    def test_audit_action_badges(self):
        AuditLog.objects.create(actor=self.boss, action=AuditAction.DELETE, model_label="sales.sale", object_id="9", object_repr="gone")
        AuditLog.objects.create(actor=self.boss, action=AuditAction.APPROVE, model_label="sales.sale", object_id="8", object_repr="ok")
        c = Client()
        c.force_login(self.boss)
        html = c.get(reverse("audit:audit_log_list")).content.decode()
        self.assertIn('rd-badge--paid">Create', html)
        self.assertIn('rd-badge--pending">Update', html)
        self.assertIn('rd-badge--cancelled">Delete', html)
        self.assertIn('rd-badge--neutral">Approve', html)


    def test_field_count_arabic_plurals(self):
        """The audit row's "N fields" blocktrans (msgid "%(n)s field") reads correctly in Arabic."""
        from django.template import Context, Template
        from django.utils import translation

        tpl = Template(
            "{% load i18n %}{% blocktrans count n=n %}{{ n }} field{% plural %}{{ n }} fields{% endblocktrans %}"
        )
        with translation.override("ar"):
            got = {n: tpl.render(Context({"n": n})).strip() for n in (1, 2, 3, 11)}
        self.assertEqual(got, {1: "1 حقل", 2: "حقلان", 3: "3 حقول", 11: "11 حقلاً"})
        with translation.override("en"):
            self.assertEqual(tpl.render(Context({"n": 2})).strip(), "2 fields")

    def test_audit_template_uses_the_field_plural_msgid(self):
        from pathlib import Path

        from django.conf import settings

        html = (Path(settings.BASE_DIR) / "templates/audit/audit_log_list.html").read_text(encoding="utf-8")
        self.assertIn("{{ n }} field{% plural %}{{ n }} fields", html)
