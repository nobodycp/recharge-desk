"""Payment methods on the page kit; Delete only for unreferenced methods.

``PaymentMethod.is_deletable`` is True only when there are no related
``sales``, ``customer_payments`` nor ``customer_payment_submissions`` (all
PROTECT FKs). These tests cover each of the three reference types plus the
kit-ified list/form pages and the existing ProtectedError safety net.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.translation import override

from accounts.models import UserProfile
from companies.models import Company, Product, ProductLine
from customers.models import Customer, CustomerPayment, CustomerPaymentSubmission
from sales.models import PaymentMethod, Sale

User = get_user_model()


def _decimal(v):
    return Decimal(str(v))


class PaymentMethodKitTests(TestCase):
    """List hides delete when referenced; edit page explains why; kit markup."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("pmmgr", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )

        cls.company = Company.objects.create(
            name="PMCo", opening_balance=_decimal(0), current_balance=_decimal(0)
        )
        line = ProductLine.objects.create(company=cls.company, name="PMLine")
        cls.product = Product.objects.create(
            line=line,
            variant_label="PMPkg",
            cost_price=_decimal(5),
            default_sell_price=_decimal(50),
        )

        cls.pm_by_sale = PaymentMethod.objects.create(name="UsedBySale")
        cls.pm_by_payment = PaymentMethod.objects.create(name="UsedByPayment")
        cls.pm_by_submission = PaymentMethod.objects.create(name="UsedBySubmission")
        cls.pm_free = PaymentMethod.objects.create(name="FreeMethod")

        Sale.objects.create(
            company=cls.company,
            product=cls.product,
            reference_number="0599000111",
            payer_name="Payer",
            payment_method=cls.pm_by_sale,
            sell_price_actual=_decimal(50),
            cost_price_snapshot=_decimal(5),
            profit_snapshot=_decimal(45),
            loss_snapshot=_decimal(0),
            status=Sale.Status.PAID,
            created_by=cls.user,
        )

        cls.customer = Customer.objects.create(name="PMCustomer", created_by=cls.user)
        CustomerPayment.objects.create(
            customer=cls.customer,
            amount=_decimal(10),
            payment_method=cls.pm_by_payment,
            created_by=cls.user,
        )
        CustomerPaymentSubmission.objects.create(
            customer=cls.customer,
            amount=_decimal(10),
            payment_method=cls.pm_by_submission,
            created_by=cls.user,
        )

    def setUp(self):
        self.client.force_login(self.user)

    # --- is_deletable --------------------------------------------------

    def test_is_deletable_false_when_referenced_by_sale(self):
        self.assertFalse(PaymentMethod.objects.get(pk=self.pm_by_sale.pk).is_deletable)

    def test_is_deletable_false_when_referenced_by_customer_payment(self):
        self.assertFalse(PaymentMethod.objects.get(pk=self.pm_by_payment.pk).is_deletable)

    def test_is_deletable_false_when_referenced_by_customer_payment_submission(self):
        self.assertFalse(PaymentMethod.objects.get(pk=self.pm_by_submission.pk).is_deletable)

    def test_is_deletable_true_when_unreferenced(self):
        self.assertTrue(PaymentMethod.objects.get(pk=self.pm_free.pk).is_deletable)

    # --- list ------------------------------------------------------------

    def test_list_hides_delete_for_referenced_methods_keeps_it_for_free(self):
        html = self.client.get(reverse("sales:payment_method_list")).content.decode()
        for referenced in (self.pm_by_sale, self.pm_by_payment, self.pm_by_submission):
            delete_url = reverse("sales:payment_method_delete", args=[referenced.pk])
            self.assertNotIn(delete_url, html)
            edit_url = reverse("sales:payment_method_edit", args=[referenced.pk])
            self.assertIn(edit_url, html)
        free_delete = reverse("sales:payment_method_delete", args=[self.pm_free.pk])
        self.assertIn(free_delete, html)

    def test_list_does_not_run_per_row_exists_query(self):
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get(reverse("sales:payment_method_list"))
        self.assertEqual(resp.status_code, 200)
        per_row_exists = [
            q["sql"]
            for q in ctx.captured_queries
            if "EXISTS" in q["sql"].upper() and "has_refs_annotated" not in q["sql"]
            and (
                "sales_sale" in q["sql"]
                or "customers_customerpayment" in q["sql"]
            )
        ]
        self.assertEqual(
            per_row_exists,
            [],
            "PaymentMethod.is_deletable probably regressed to per-row EXISTS.",
        )

    def test_list_uses_kit(self):
        r = self.client.get(reverse("sales:payment_method_list"))
        self.assertContains(r, "rd-page-head")
        self.assertContains(r, "rd-badge")

    # --- form --------------------------------------------------------------

    def test_edit_page_explains_why_delete_is_unavailable(self):
        sentence = (
            "This payment method can't be deleted because sales or customer "
            "payments still reference it. Turn off “Active” and save to hide "
            "it on the sales screen."
        )
        with override("en"):
            referenced = self.client.get(
                reverse("sales:payment_method_edit", args=[self.pm_by_sale.pk])
            )
            free = self.client.get(
                reverse("sales:payment_method_edit", args=[self.pm_free.pk])
            )
        self.assertContains(referenced, sentence)
        self.assertNotContains(
            referenced, reverse("sales:payment_method_delete", args=[self.pm_by_sale.pk])
        )
        self.assertContains(free, reverse("sales:payment_method_delete", args=[self.pm_free.pk]))
        self.assertContains(free, "Delete payment method")

    def test_form_uses_kit(self):
        r = self.client.get(reverse("sales:payment_method_edit", args=[self.pm_free.pk]))
        self.assertContains(r, "rd-page-head")
        self.assertContains(r, "rd-field")
        self.assertNotContains(r, "app-card")
        self.assertNotContains(r, 'class="card')

    def test_form_posts_icon_clear_for_clearable_file_input(self):
        r = self.client.get(reverse("sales:payment_method_edit", args=[self.pm_free.pk]))
        self.assertContains(r, 'name="icon-clear"', count=0)  # no icon uploaded yet
        self.assertContains(r, "id_icon")

    # --- ProtectedError safety net stays unchanged --------------------------

    def test_forced_delete_on_referenced_method_still_shows_existing_toast(self):
        resp = self.client.post(
            reverse("sales:payment_method_delete", args=[self.pm_by_sale.pk]),
            follow=True,
        )
        self.assertContains(
            resp,
            "Cannot delete “UsedBySale” because existing sales or customer "
            "payments still reference it. Mark it inactive instead, or "
            "first move those records to another method.",
        )
        self.assertTrue(PaymentMethod.objects.filter(pk=self.pm_by_sale.pk).exists())

    def test_delete_still_works_for_unreferenced_method(self):
        pm = PaymentMethod.objects.create(name="ToDelete")
        resp = self.client.post(
            reverse("sales:payment_method_delete", args=[pm.pk]), follow=True
        )
        self.assertFalse(PaymentMethod.objects.filter(pk=pm.pk).exists())
        self.assertContains(resp, "was deleted.")


class PaymentMethodFormWidgetTests(TestCase):
    """Kit supplies widget classes; the form only keeps `role=switch`."""

    def test_widgets_are_not_hand_annotated_with_bootstrap_classes(self):
        from sales.forms import PaymentMethodForm

        form = PaymentMethodForm()
        self.assertNotIn("class", form.fields["name"].widget.attrs)
        self.assertNotIn("class", form.fields["icon"].widget.attrs)
        self.assertEqual(form.fields["is_active"].widget.attrs.get("role"), "switch")
        self.assertNotIn("class", form.fields["is_active"].widget.attrs)
