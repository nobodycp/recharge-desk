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
from sales.models import CompanyBalanceTransaction
from sales.services import (
    cleanup_orphan_sale_balance_transactions,
    create_sale,
    find_orphan_sale_balance_transactions,
    update_sale_fields,
)

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


class CleanupOrphanLedgerTests(TestCase):
    """find_orphan / cleanup_orphan must purge SALE-typed rows whose Sale is gone."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("mgr_orphan", password="x")
        cls.company = Company.objects.create(
            name="OrphanCo",
            opening_balance=Decimal("1000"),
            current_balance=Decimal("1000"),
        )
        cls.line = ProductLine.objects.create(company=cls.company, name="L")
        cls.product = Product.objects.create(
            line=cls.line,
            variant_label="P",
            cost_price=Decimal("5"),
            default_sell_price=Decimal("20"),
        )
        cls.pm = PaymentMethod.objects.create(name="Cash")

    def _make_sale(self, sell=20):
        return create_sale(
            company=self.company,
            product=self.product,
            reference_number=f"o-{Sale.objects.count() + 1}",
            payer_name="P",
            payment_method=self.pm,
            sell_price_actual=Decimal(str(sell)),
            notes="",
            user=self.user,
        )

    def test_finder_ignores_live_sales(self):
        s = self._make_sale()
        self.assertFalse(find_orphan_sale_balance_transactions().exists())
        s.delete()
        self.assertTrue(find_orphan_sale_balance_transactions().exists())

    def test_dry_run_does_not_touch_db(self):
        s = self._make_sale()
        before_balance = Company.objects.get(pk=self.company.pk).current_balance
        before_count = CompanyBalanceTransaction.objects.count()
        s.delete()
        summary = cleanup_orphan_sale_balance_transactions(dry_run=True)
        self.assertEqual(summary["orphan_count"], 1)
        self.assertEqual(summary["companies_affected"], 1)
        # Net refund = +5 (the original cost)
        self.assertEqual(summary["net_refund_total"], Decimal("5"))
        # Database state must be unchanged.
        self.company.refresh_from_db()
        self.assertEqual(self.company.current_balance, before_balance)
        self.assertEqual(CompanyBalanceTransaction.objects.count(), before_count)

    def test_apply_refunds_balance_and_removes_orphans(self):
        s = self._make_sale()
        # After create_sale, balance was reduced by the cost (5).
        self.company.refresh_from_db()
        balance_after_create = self.company.current_balance
        s.delete()
        # Hard delete leaves the DEDUCTION orphan; balance is unchanged.
        self.company.refresh_from_db()
        self.assertEqual(self.company.current_balance, balance_after_create)

        summary = cleanup_orphan_sale_balance_transactions(user=self.user)

        # The orphan row was removed and a single REVERSAL audit row added.
        self.assertEqual(summary["orphan_count"], 1)
        self.assertEqual(summary["net_refund_total"], Decimal("5"))
        self.assertFalse(find_orphan_sale_balance_transactions().exists())

        audit = CompanyBalanceTransaction.objects.filter(
            company=self.company,
            entry_type=CompanyBalanceTransaction.EntryType.REVERSAL,
            reference_type=CompanyBalanceTransaction.ReferenceType.MANUAL,
        ).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.amount, Decimal("5"))

        # Balance should be back to the pre-sale value.
        self.company.refresh_from_db()
        self.assertEqual(self.company.current_balance, balance_after_create + Decimal("5"))

    def test_company_filter_isolates_cleanup(self):
        other = Company.objects.create(
            name="Other", opening_balance=Decimal("0"), current_balance=Decimal("0")
        )
        line2 = ProductLine.objects.create(company=other, name="L2")
        prod2 = Product.objects.create(
            line=line2, variant_label="P", cost_price=Decimal("3"), default_sell_price=Decimal("10")
        )
        s1 = self._make_sale()
        s2 = create_sale(
            company=other, product=prod2, reference_number="x", payer_name="p",
            payment_method=self.pm, sell_price_actual=Decimal("10"), notes="", user=self.user,
        )
        s1.delete()
        s2.delete()
        # Restrict cleanup to only `self.company`.
        summary = cleanup_orphan_sale_balance_transactions(user=self.user, company=self.company)
        self.assertEqual(summary["orphan_count"], 1)
        # The other company still has its orphan.
        remaining = find_orphan_sale_balance_transactions(company=other)
        self.assertEqual(remaining.count(), 1)

    def test_no_orphans_returns_zero_summary(self):
        summary = cleanup_orphan_sale_balance_transactions()
        self.assertEqual(summary["orphan_count"], 0)
        self.assertEqual(summary["companies_affected"], 0)


class CleanupOrphanLedgerCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("mgr_orphan_cmd", password="x")
        cls.company = Company.objects.create(
            name="CmdCo", opening_balance=Decimal("1000"), current_balance=Decimal("1000")
        )
        cls.line = ProductLine.objects.create(company=cls.company, name="L")
        cls.product = Product.objects.create(
            line=cls.line, variant_label="P", cost_price=Decimal("5"), default_sell_price=Decimal("20")
        )
        cls.pm = PaymentMethod.objects.create(name="Cash")

    def test_dry_run_reports_but_does_not_change(self):
        from io import StringIO
        from django.core.management import call_command

        s = create_sale(
            company=self.company, product=self.product, reference_number="cmd-1",
            payer_name="p", payment_method=self.pm, sell_price_actual=Decimal("20"),
            notes="", user=self.user,
        )
        s.delete()
        before = CompanyBalanceTransaction.objects.count()
        out = StringIO()
        call_command("cleanup_orphan_ledger", "--dry-run", stdout=out)
        self.assertIn("Would delete 1 row", out.getvalue())
        self.assertEqual(CompanyBalanceTransaction.objects.count(), before)

    def test_apply_removes_orphans(self):
        from io import StringIO
        from django.core.management import call_command

        s = create_sale(
            company=self.company, product=self.product, reference_number="cmd-2",
            payer_name="p", payment_method=self.pm, sell_price_actual=Decimal("20"),
            notes="", user=self.user,
        )
        s.delete()
        out = StringIO()
        call_command("cleanup_orphan_ledger", stdout=out)
        self.assertIn("Removed 1 orphan", out.getvalue())
        self.assertFalse(find_orphan_sale_balance_transactions().exists())
