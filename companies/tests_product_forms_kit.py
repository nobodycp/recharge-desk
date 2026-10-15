"""Product line and package (variant) forms on the page kit.

``ProductLine.is_deletable`` / ``Product.is_deletable`` are True only when
there are no ``Sale`` rows referencing them (PROTECT-style business rule,
not an FK constraint). These tests cover the kit-ified line/variant forms:
delete hidden + explanation when referenced, the variant archive button
class, and that posting still works with the stripped widgets.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils.translation import override

from accounts.models import UserProfile
from companies.models import Company, Product, ProductLine
from sales.models import PaymentMethod, Sale

User = get_user_model()


def _decimal(v):
    return Decimal(str(v))


class ProductLineFormKitTests(TestCase):
    """Line form: kit markup, delete hidden + explanation when the line has sales."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("plmgr", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        cls.company = Company.objects.create(
            name="LineCo", opening_balance=_decimal(0), current_balance=_decimal(0)
        )
        cls.method = PaymentMethod.objects.create(name="LineCash")

        cls.line_with_sales = ProductLine.objects.create(company=cls.company, name="HasSales")
        cls.product_with_sales = Product.objects.create(
            line=cls.line_with_sales,
            variant_label="Pkg",
            cost_price=_decimal(5),
            default_sell_price=_decimal(50),
        )
        Sale.objects.create(
            company=cls.company,
            product=cls.product_with_sales,
            reference_number="0599000222",
            payer_name="Payer",
            payment_method=cls.method,
            sell_price_actual=_decimal(50),
            cost_price_snapshot=_decimal(5),
            profit_snapshot=_decimal(45),
            loss_snapshot=_decimal(0),
            status=Sale.Status.PAID,
            created_by=cls.user,
        )

        cls.line_free = ProductLine.objects.create(company=cls.company, name="FreeLine")

    def setUp(self):
        self.client.force_login(self.user)

    def test_is_deletable_false_when_referenced_by_sale(self):
        self.assertFalse(ProductLine.objects.get(pk=self.line_with_sales.pk).is_deletable)

    def test_is_deletable_true_when_unreferenced(self):
        self.assertTrue(ProductLine.objects.get(pk=self.line_free.pk).is_deletable)

    def test_edit_page_explains_why_delete_is_unavailable(self):
        sentence = (
            "This product line can't be deleted because it has sales. Turn "
            "off “Active” and save to hide it from new sales."
        )
        with override("en"):
            referenced = self.client.get(
                reverse("companies:product_line_edit", args=[self.line_with_sales.pk])
            )
            free = self.client.get(
                reverse("companies:product_line_edit", args=[self.line_free.pk])
            )
        self.assertContains(referenced, sentence)
        self.assertNotContains(
            referenced, reverse("companies:product_line_delete", args=[self.line_with_sales.pk])
        )
        self.assertNotContains(referenced, "Delete unavailable (has sales)")
        self.assertContains(
            free, reverse("companies:product_line_delete", args=[self.line_free.pk])
        )
        self.assertContains(free, "Delete product line")

    def test_form_uses_kit(self):
        r = self.client.get(reverse("companies:product_line_edit", args=[self.line_free.pk]))
        self.assertContains(r, "rd-page-head")
        self.assertContains(r, "rd-field")
        self.assertNotContains(r, "app-card")
        self.assertNotContains(r, 'class="card')

    def test_form_posts_still_work_with_stripped_widgets(self):
        resp = self.client.post(
            reverse("companies:product_line_edit", args=[self.line_free.pk]),
            data={
                "company": self.company.pk,
                "name": "FreeLine Renamed",
                "sort_order": 0,
                "estimated_unit_cost": "",
                "is_active": "on",
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.line_free.refresh_from_db()
        self.assertEqual(self.line_free.name, "FreeLine Renamed")


class ProductLineFormWidgetTests(TestCase):
    """Kit supplies widget classes; the form only keeps `role=switch`."""

    def test_widgets_are_not_hand_annotated_with_bootstrap_classes(self):
        from companies.forms import ProductLineForm

        form = ProductLineForm()
        self.assertNotIn("class", form.fields["name"].widget.attrs)
        self.assertNotIn("class", form.fields["icon"].widget.attrs)
        self.assertNotIn("class", form.fields["company"].widget.attrs)
        self.assertNotIn("class", form.fields["sort_order"].widget.attrs)
        self.assertNotIn("class", form.fields["estimated_unit_cost"].widget.attrs)
        self.assertNotIn("class", form.fields["allow_partial_days"].widget.attrs)
        self.assertEqual(form.fields["allow_partial_days"].widget.attrs.get("role"), "switch")
        self.assertNotIn("class", form.fields["is_active"].widget.attrs)
        self.assertEqual(form.fields["is_active"].widget.attrs.get("role"), "switch")


class ProductVariantFormKitTests(TestCase):
    """Variant form: delete/archive/restore zone and header subtitle."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("pvmgr", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        cls.company = Company.objects.create(
            name="VariantCo", opening_balance=_decimal(0), current_balance=_decimal(0)
        )
        cls.method = PaymentMethod.objects.create(name="VariantCash")
        cls.line = ProductLine.objects.create(company=cls.company, name="VLine")

        cls.variant_with_sales = Product.objects.create(
            line=cls.line,
            variant_label="HasSalesPkg",
            cost_price=_decimal(5),
            default_sell_price=_decimal(50),
        )
        Sale.objects.create(
            company=cls.company,
            product=cls.variant_with_sales,
            reference_number="0599000333",
            payer_name="Payer",
            payment_method=cls.method,
            sell_price_actual=_decimal(50),
            cost_price_snapshot=_decimal(5),
            profit_snapshot=_decimal(45),
            loss_snapshot=_decimal(0),
            status=Sale.Status.PAID,
            created_by=cls.user,
        )

        cls.variant_free = Product.objects.create(
            line=cls.line,
            variant_label="FreePkg",
            cost_price=_decimal(5),
            default_sell_price=_decimal(50),
        )

        cls.variant_archived = Product.objects.create(
            line=cls.line,
            variant_label="ArchivedPkg",
            cost_price=_decimal(5),
            default_sell_price=_decimal(50),
            is_active=False,
        )
        Sale.objects.create(
            company=cls.company,
            product=cls.variant_archived,
            reference_number="0599000444",
            payer_name="Payer",
            payment_method=cls.method,
            sell_price_actual=_decimal(50),
            cost_price_snapshot=_decimal(5),
            profit_snapshot=_decimal(45),
            loss_snapshot=_decimal(0),
            status=Sale.Status.PAID,
            created_by=cls.user,
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_header_shows_company_and_line_subtitle(self):
        r = self.client.get(reverse("companies:product_variant_edit", args=[self.variant_free.pk]))
        self.assertContains(r, "VariantCo — VLine")

    def test_variant_with_sales_shows_archive_not_delete(self):
        r = self.client.get(
            reverse("companies:product_variant_edit", args=[self.variant_with_sales.pk])
        )
        self.assertNotContains(
            r, reverse("companies:product_variant_delete", args=[self.variant_with_sales.pk])
        )
        self.assertContains(
            r, reverse("companies:product_variant_archive", args=[self.variant_with_sales.pk])
        )
        self.assertContains(r, "Archive package")
        self.assertContains(
            r, "Has sales — archive to hide it without affecting history."
        )

    def test_archive_button_is_not_outline_warning(self):
        r = self.client.get(
            reverse("companies:product_variant_edit", args=[self.variant_with_sales.pk])
        ).content.decode()
        self.assertNotIn("btn-outline-warning", r)
        self.assertIn("btn-outline-secondary", r)

    def test_free_variant_shows_delete(self):
        r = self.client.get(reverse("companies:product_variant_edit", args=[self.variant_free.pk]))
        self.assertContains(
            r, reverse("companies:product_variant_delete", args=[self.variant_free.pk])
        )
        self.assertContains(r, "Delete package")

    def test_archived_variant_shows_restore(self):
        r = self.client.get(
            reverse("companies:product_variant_edit", args=[self.variant_archived.pk])
        )
        self.assertContains(
            r, reverse("companies:product_variant_unarchive", args=[self.variant_archived.pk])
        )
        self.assertContains(r, "Restore package")
        self.assertContains(r, "Archived — hidden from new sales.")

    def test_form_uses_kit(self):
        r = self.client.get(reverse("companies:product_variant_edit", args=[self.variant_free.pk]))
        self.assertContains(r, "rd-page-head")
        self.assertContains(r, "rd-field")
        self.assertNotContains(r, "app-card")
        self.assertNotContains(r, 'class="card')

    def test_form_posts_still_work_with_stripped_widgets(self):
        resp = self.client.post(
            reverse("companies:product_variant_edit", args=[self.variant_free.pk]),
            data={
                "variant_label": "FreePkg Renamed",
                "cost_price": "5.00",
                "default_sell_price": "50.00",
                "base_days": 30,
                "is_active": "on",
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.variant_free.refresh_from_db()
        self.assertEqual(self.variant_free.variant_label, "FreePkg Renamed")


class ProductVariantFormWidgetTests(TestCase):
    """Kit supplies widget classes; the form only keeps `role=switch`."""

    def test_widgets_are_not_hand_annotated_with_bootstrap_classes(self):
        from companies.forms import ProductVariantForm

        form = ProductVariantForm()
        self.assertNotIn("class", form.fields["variant_label"].widget.attrs)
        self.assertNotIn("class", form.fields["cost_price"].widget.attrs)
        self.assertNotIn("class", form.fields["default_sell_price"].widget.attrs)
        self.assertNotIn("class", form.fields["base_days"].widget.attrs)
        self.assertNotIn("class", form.fields["is_active"].widget.attrs)
        self.assertEqual(form.fields["is_active"].widget.attrs.get("role"), "switch")
