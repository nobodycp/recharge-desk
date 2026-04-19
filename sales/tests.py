from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from companies.models import Company, Product, ProductLine
from sales.models import PaymentMethod, Sale
from sales.payer_lookup import (
    latest_payer_for_reference,
    latest_sale_for_reference,
    payer_name_suggestions,
)
from sales.pricing import ESIM_EXTRA_COST, effective_cost_for_product, loss_snapshot_for_sale
from sales.services import create_sale, update_sale_fields

User = get_user_model()


class PayerAssistTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(
            name="Co",
            opening_balance=Decimal("1000"),
            current_balance=Decimal("1000"),
        )
        cls.line = ProductLine.objects.create(company=cls.company, name="L")
        cls.product = Product.objects.create(
            line=cls.line,
            variant_label="P",
            cost_price=Decimal("5"),
            default_sell_price=Decimal("10"),
        )
        cls.pm = PaymentMethod.objects.create(name="Cash")
        cls.user = User.objects.create_user("emp", password="x")

    def _make_sale(self, ref, payer, status=Sale.Status.PAID):
        return Sale.objects.create(
            company=self.company,
            product=self.product,
            reference_number=ref,
            payer_name=payer,
            payment_method=self.pm,
            sell_price_actual=Decimal("10"),
            cost_price_snapshot=Decimal("5"),
            profit_snapshot=Decimal("5"),
            loss_snapshot=Decimal("0"),
            status=status,
            created_by=self.user,
        )

    def test_latest_payer_prefers_recent(self):
        self._make_sale("059111", "Old Name")
        self._make_sale("059111", "New Name")
        self.assertEqual(latest_payer_for_reference("059111"), "New Name")

    def test_latest_payer_excludes_cancelled(self):
        s = self._make_sale("059222", "Gone")
        s.status = Sale.Status.CANCELLED
        s.save(update_fields=["status"])
        self.assertIsNone(latest_payer_for_reference("059222"))

    def test_suggestions_include_matches(self):
        self._make_sale("a", "Mohammad Ahmad")
        self._make_sale("b", "Mohammad Ahmad")
        self._make_sale("c", "Mohammad Saleh")
        items = payer_name_suggestions("Moh")
        names = [x["name"] for x in items]
        self.assertIn("Mohammad Ahmad", names)
        self.assertIn("Mohammad Saleh", names)
        ahmad = next(x for x in items if x["name"] == "Mohammad Ahmad")
        self.assertGreaterEqual(ahmad["count"], 2)

    def test_api_requires_login(self):
        c = Client()
        r = c.get(reverse("sales:api_payer_by_number"), {"number": "059"})
        self.assertEqual(r.status_code, 302)

    def test_api_payer_by_number_json(self):
        self._make_sale("059333", "Api User")
        c = Client()
        c.force_login(self.user)
        r = c.get(reverse("sales:api_payer_by_number"), {"number": "059333"})
        self.assertEqual(r.status_code, 200)
        payload = r.json()
        self.assertEqual(payload["payer_name"], "Api User")
        self.assertEqual(payload["company_id"], self.company.pk)
        self.assertEqual(payload["product_id"], self.product.pk)

    def test_latest_sale_snapshot_returns_company_and_product(self):
        self._make_sale("059444", "Snap User")
        snap = latest_sale_for_reference("059444")
        self.assertIsNotNone(snap)
        self.assertEqual(snap["payer_name"], "Snap User")
        self.assertEqual(snap["company_id"], self.company.pk)
        self.assertEqual(snap["product_id"], self.product.pk)

    def test_latest_sale_snapshot_is_none_for_unknown(self):
        self.assertIsNone(latest_sale_for_reference("nope-xxx"))

    def test_api_unknown_number_returns_empty_payload(self):
        c = Client()
        c.force_login(self.user)
        r = c.get(reverse("sales:api_payer_by_number"), {"number": "000999"})
        self.assertEqual(r.status_code, 200)
        payload = r.json()
        self.assertIsNone(payload["payer_name"])
        self.assertIsNone(payload["company_id"])
        self.assertIsNone(payload["product_id"])

    def test_api_suggestions_json(self):
        self._make_sale("x", "Suggest Me")
        c = Client()
        c.force_login(self.user)
        r = c.get(reverse("sales:api_payer_name_suggestions"), {"q": "Sug"})
        self.assertEqual(r.status_code, 200)
        names = [x["name"] for x in r.json()["suggestions"]]
        self.assertIn("Suggest Me", names)


class EsimSaleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(
            name="EsimCo",
            opening_balance=Decimal("1000"),
            current_balance=Decimal("1000"),
        )
        cls.line = ProductLine.objects.create(company=cls.company, name="LineE")
        cls.product = Product.objects.create(
            line=cls.line,
            variant_label="Pkg",
            cost_price=Decimal("30"),
            default_sell_price=Decimal("50"),
        )
        cls.pm = PaymentMethod.objects.create(name="CashE")
        cls.user = User.objects.create_user("esim_emp", password="x")

    def test_effective_cost_for_product(self):
        self.assertEqual(
            effective_cost_for_product(self.product, is_esim=False),
            Decimal("30"),
        )
        self.assertEqual(
            effective_cost_for_product(self.product, is_esim=True),
            Decimal("30") + ESIM_EXTRA_COST,
        )

    def test_loss_snapshot_for_zero_price(self):
        self.assertEqual(
            loss_snapshot_for_sale(
                sell_price_actual=Decimal("0"),
                cost_price_snapshot=Decimal("30"),
            ),
            Decimal("30"),
        )
        self.assertEqual(
            loss_snapshot_for_sale(
                sell_price_actual=Decimal("10"),
                cost_price_snapshot=Decimal("30"),
            ),
            Decimal("0"),
        )

    def test_create_sale_esim_snapshot_and_balance(self):
        sale = create_sale(
            company=self.company,
            product=self.product,
            reference_number="0590000000",
            payer_name="Test",
            payment_method=self.pm,
            sell_price_actual=Decimal("50"),
            notes="",
            user=self.user,
            is_esim=True,
        )
        self.assertTrue(sale.is_esim)
        self.assertEqual(sale.cost_price_snapshot, Decimal("35"))
        self.assertEqual(sale.profit_snapshot, Decimal("15"))
        self.assertEqual(sale.loss_snapshot, Decimal("0"))
        self.company.refresh_from_db()
        self.assertEqual(self.company.current_balance, Decimal("1000") - Decimal("35"))

    def test_create_sale_non_esim(self):
        sale = create_sale(
            company=self.company,
            product=self.product,
            reference_number="0590000001",
            payer_name="Test",
            payment_method=self.pm,
            sell_price_actual=Decimal("50"),
            notes="",
            user=self.user,
            is_esim=False,
        )
        self.assertFalse(sale.is_esim)
        self.assertEqual(sale.cost_price_snapshot, Decimal("30"))
        self.assertEqual(sale.profit_snapshot, Decimal("20"))
        self.assertEqual(sale.loss_snapshot, Decimal("0"))

    def test_create_sale_zero_sell_price_allowed(self):
        sale = create_sale(
            company=self.company,
            product=self.product,
            reference_number="0590000999",
            payer_name="Zero",
            payment_method=self.pm,
            sell_price_actual=Decimal("0"),
            notes="",
            user=self.user,
            is_esim=False,
        )
        self.assertEqual(sale.sell_price_actual, Decimal("0"))
        self.assertEqual(sale.profit_snapshot, Decimal("-30"))
        self.assertEqual(sale.loss_snapshot, Decimal("30"))

    def test_create_sale_zero_sell_with_esim_records_full_cost_as_loss(self):
        sale = create_sale(
            company=self.company,
            product=self.product,
            reference_number="0590000888",
            payer_name="ZeroE",
            payment_method=self.pm,
            sell_price_actual=Decimal("0"),
            notes="",
            user=self.user,
            is_esim=True,
        )
        self.assertEqual(sale.cost_price_snapshot, Decimal("35"))
        self.assertEqual(sale.loss_snapshot, Decimal("35"))
        self.assertEqual(sale.profit_snapshot, Decimal("-35"))

    def test_update_sale_fields_swaps_payment_method_without_touching_balance(self):
        sale = create_sale(
            company=self.company,
            product=self.product,
            reference_number="0590000077",
            payer_name="Old Payer",
            payment_method=self.pm,
            sell_price_actual=Decimal("50"),
            notes="",
            user=self.user,
            is_esim=False,
        )
        self.company.refresh_from_db()
        balance_after_create = self.company.current_balance

        new_pm = PaymentMethod.objects.create(name="Card")
        updated = update_sale_fields(
            sale=sale,
            payment_method=new_pm,
            payer_name="Real Payer  ",
            reference_number="  0599999999 ",
            sell_price_actual=Decimal("40"),
            notes="  swapped to card  ",
            user=self.user,
        )

        self.assertEqual(updated.payment_method_id, new_pm.id)
        self.assertEqual(updated.payer_name, "Real Payer")
        self.assertEqual(updated.reference_number, "0599999999")
        self.assertEqual(updated.sell_price_actual, Decimal("40"))
        self.assertEqual(updated.notes, "swapped to card")
        # cost snapshot and balance must NOT shift on a safe edit.
        self.assertEqual(updated.cost_price_snapshot, Decimal("30"))
        self.assertEqual(updated.profit_snapshot, Decimal("10"))
        self.assertEqual(updated.loss_snapshot, Decimal("0"))
        self.company.refresh_from_db()
        self.assertEqual(self.company.current_balance, balance_after_create)

    def test_update_sale_fields_recomputes_loss_when_price_drops_to_zero(self):
        sale = create_sale(
            company=self.company,
            product=self.product,
            reference_number="0599000044",
            payer_name="X",
            payment_method=self.pm,
            sell_price_actual=Decimal("50"),
            notes="",
            user=self.user,
            is_esim=False,
        )
        updated = update_sale_fields(
            sale=sale,
            payment_method=self.pm,
            payer_name="X",
            reference_number=sale.reference_number,
            sell_price_actual=Decimal("0"),
            notes="",
            user=self.user,
        )
        self.assertEqual(updated.profit_snapshot, Decimal("-30"))
        self.assertEqual(updated.loss_snapshot, Decimal("30"))
