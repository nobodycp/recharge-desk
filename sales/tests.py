from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from companies.models import Company, Product, ProductLine
from customers.models import Customer
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

    def test_suggestions_include_active_customers(self):
        Customer.objects.create(name="Suggest Customer", created_by=self.user)
        items = payer_name_suggestions("Sugges")
        names = [x["name"] for x in items]
        self.assertIn("Suggest Customer", names)
        row = next(x for x in items if x["name"] == "Suggest Customer")
        self.assertEqual(row["count"], 0)

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


class EmployeeRecentSalesTests(TestCase):
    """The employee-facing 'today's entries' page lets a cashier list,
    edit and delete their own sales — but only while management hasn't
    acted on them yet."""

    @classmethod
    def setUpTestData(cls):
        from accounts.models import UserProfile

        cls.company = Company.objects.create(
            name="EmpRecCo",
            opening_balance=Decimal("1000"),
            current_balance=Decimal("1000"),
        )
        cls.line = ProductLine.objects.create(company=cls.company, name="Line")
        cls.product = Product.objects.create(
            line=cls.line,
            variant_label="Pkg",
            cost_price=Decimal("5"),
            default_sell_price=Decimal("10"),
        )
        cls.pm = PaymentMethod.objects.create(name="Cash")
        cls.pm2 = PaymentMethod.objects.create(name="Card")
        cls.emp = User.objects.create_user("emp_rec", password="x")
        UserProfile.objects.update_or_create(
            user=cls.emp,
            defaults={"role": UserProfile.Role.EMPLOYEE, "is_active_profile": True},
        )
        cls.other = User.objects.create_user("other_emp", password="x")
        UserProfile.objects.update_or_create(
            user=cls.other,
            defaults={"role": UserProfile.Role.EMPLOYEE, "is_active_profile": True},
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.emp)

    def _make_sale(self, *, user=None, payer="P", price=Decimal("10")):
        return create_sale(
            company=self.company,
            product=self.product,
            reference_number="0590000000",
            payer_name=payer,
            payment_method=self.pm,
            sell_price_actual=price,
            notes="",
            user=user or self.emp,
        )

    def test_modifiable_property_matrix(self):
        s = self._make_sale()
        self.assertTrue(s.is_employee_modifiable)
        s.status = Sale.Status.PAID
        self.assertFalse(s.is_employee_modifiable)
        s.status = Sale.Status.CANCELLED
        self.assertFalse(s.is_employee_modifiable)
        s.status = Sale.Status.WRITTEN_OFF
        self.assertFalse(s.is_employee_modifiable)
        s.status = Sale.Status.AWAITING
        self.assertTrue(s.is_employee_modifiable)
        s.status = Sale.Status.PENDING
        s.on_account = True
        self.assertFalse(s.is_employee_modifiable)

    def test_recent_page_lists_only_today_and_only_own(self):
        from datetime import timedelta

        from django.utils import timezone

        own = self._make_sale(payer="Mine")
        foreign = self._make_sale(user=self.other, payer="NotMine")
        old = self._make_sale(payer="Yesterday")
        Sale.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=2)
        )

        url = reverse("sales:employee_recent_sales")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Mine")
        self.assertNotContains(resp, "NotMine")
        self.assertNotContains(resp, "Yesterday")
        # Edit/delete affordances should be present for a fresh PENDING sale.
        self.assertContains(
            resp, reverse("sales:employee_sale_edit", args=[own.pk])
        )
        self.assertContains(
            resp, reverse("sales:employee_sale_delete", args=[own.pk])
        )

    def test_employee_can_delete_own_pending_sale(self):
        s = self._make_sale()
        before = self.company.current_balance
        # Sale should have decremented the supplier balance.
        self.company.refresh_from_db()
        self.assertNotEqual(self.company.current_balance, before)

        url = reverse("sales:employee_sale_delete", args=[s.pk])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Sale.objects.filter(pk=s.pk).exists())
        # Supplier balance must be net-zero again.
        self.company.refresh_from_db()
        self.assertEqual(self.company.current_balance, Decimal("1000"))

    def test_employee_can_delete_paid_sale_within_last_ten(self):
        s = self._make_sale()
        s.status = Sale.Status.PAID
        s.save(update_fields=["status"])
        url = reverse("sales:employee_sale_delete", args=[s.pk])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Sale.objects.filter(pk=s.pk).exists())

    def test_employee_cannot_delete_paid_sale_outside_last_ten(self):
        sales = [self._make_sale(payer=f"P{i}") for i in range(11)]
        oldest = sales[0]
        oldest.status = Sale.Status.PAID
        oldest.save(update_fields=["status"])
        url = reverse("sales:employee_sale_delete", args=[oldest.pk])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Sale.objects.filter(pk=oldest.pk).exists())

    def test_employee_cannot_touch_another_users_sale(self):
        s = self._make_sale(user=self.other)
        del_url = reverse("sales:employee_sale_delete", args=[s.pk])
        edit_url = reverse("sales:employee_sale_edit", args=[s.pk])
        self.assertEqual(self.client.post(del_url).status_code, 404)
        self.assertEqual(self.client.get(edit_url).status_code, 404)
        self.assertTrue(Sale.objects.filter(pk=s.pk).exists())

    def test_employee_can_edit_own_pending_sale(self):
        s = self._make_sale(payer="Old")
        url = reverse("sales:employee_sale_edit", args=[s.pk])
        resp = self.client.post(
            url,
            data={
                "payment_method": self.pm2.pk,
                "payer_name": "Fixed",
                "reference_number": "0590000111",
                "sell_price_actual": "12.00",
                "notes": "",
            },
        )
        self.assertEqual(resp.status_code, 302)
        s.refresh_from_db()
        self.assertEqual(s.payer_name, "Fixed")
        self.assertEqual(s.payment_method_id, self.pm2.pk)
        self.assertEqual(s.reference_number, "0590000111")
        self.assertEqual(s.sell_price_actual, Decimal("12.00"))

    def test_employee_edit_blocked_outside_last_ten(self):
        sales = [self._make_sale(payer=f"P{i}") for i in range(11)]
        oldest = sales[0]
        oldest.status = Sale.Status.PAID
        oldest.save(update_fields=["status"])
        url = reverse("sales:employee_sale_edit", args=[oldest.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("sales:employee_recent_sales"))

        newest = sales[-1]
        newest.status = Sale.Status.PAID
        newest.save(update_fields=["status"])
        resp = self.client.get(reverse("sales:employee_sale_edit", args=[newest.pk]))
        self.assertEqual(resp.status_code, 200)

    def test_htmx_delete_returns_empty_200_for_row_swap(self):
        s = self._make_sale()
        url = reverse("sales:employee_sale_delete", args=[s.pk])
        resp = self.client.post(url, HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b"")
        self.assertFalse(Sale.objects.filter(pk=s.pk).exists())

    def test_htmx_delete_blocked_keeps_row_and_emits_error_trigger(self):
        sales = [self._make_sale(payer=f"H{i}") for i in range(11)]
        oldest = sales[0]
        oldest.status = Sale.Status.PAID
        oldest.save(update_fields=["status"])
        url = reverse("sales:employee_sale_delete", args=[oldest.pk])
        resp = self.client.post(url, HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["HX-Reswap"], "none")
        self.assertIn("rdSaleActionError", resp["HX-Trigger"])
        self.assertTrue(Sale.objects.filter(pk=oldest.pk).exists())

    def test_view_all_link_appears_on_entry_page(self):
        resp = self.client.get(reverse("sales:employee_entry"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse("sales:employee_recent_sales"))

    def test_date_filter_returns_past_day(self):
        from datetime import timedelta

        from django.utils import timezone

        # Use the local TZ throughout so the date math agrees with the
        # ORM's `created_at__date` lookup (which honours TIME_ZONE).
        yesterday = timezone.localdate() - timedelta(days=1)
        old = self._make_sale(payer="OldPayer")
        Sale.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=1)
        )
        # Today entry that must NOT appear when we ask for yesterday only.
        self._make_sale(payer="TodayPayer")

        url = reverse("sales:employee_recent_sales")
        resp = self.client.get(
            url,
            {
                "date_from": yesterday.isoformat(),
                "date_to": yesterday.isoformat(),
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "OldPayer")
        self.assertNotContains(resp, "TodayPayer")

    def test_date_range_filter_includes_endpoints(self):
        from datetime import timedelta

        from django.utils import timezone

        old = self._make_sale(payer="ThreeDayOld")
        Sale.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=3)
        )
        recent = self._make_sale(payer="OneDayOld")
        Sale.objects.filter(pk=recent.pk).update(
            created_at=timezone.now() - timedelta(days=1)
        )
        self._make_sale(payer="HappenedToday")

        d_from = timezone.localdate() - timedelta(days=2)
        d_to = timezone.localdate()

        resp = self.client.get(
            reverse("sales:employee_recent_sales"),
            {"date_from": d_from.isoformat(), "date_to": d_to.isoformat()},
        )
        self.assertContains(resp, "OneDayOld")
        self.assertContains(resp, "HappenedToday")
        self.assertNotContains(resp, "ThreeDayOld")

    def test_date_filter_still_scoped_to_current_user(self):
        from datetime import timedelta

        from django.utils import timezone

        foreign = self._make_sale(user=self.other, payer="OtherUserOld")
        Sale.objects.filter(pk=foreign.pk).update(
            created_at=timezone.now() - timedelta(days=1)
        )
        d = timezone.localdate() - timedelta(days=1)
        resp = self.client.get(
            reverse("sales:employee_recent_sales"),
            {"date_from": d.isoformat(), "date_to": d.isoformat()},
        )
        self.assertNotContains(resp, "OtherUserOld")

    def test_invalid_range_surfaces_form_error(self):
        resp = self.client.get(
            reverse("sales:employee_recent_sales"),
            {"date_from": "2026-04-20", "date_to": "2026-04-10"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Date from")  # form still rendered

    def test_text_search_matches_reference_or_payer(self):
        # Three sales today with distinct references and payers.
        a = self._make_sale(payer="Hazem Salah")
        Sale.objects.filter(pk=a.pk).update(reference_number="0590000111")
        b = self._make_sale(payer="Other Person")
        Sale.objects.filter(pk=b.pk).update(reference_number="0590000222")
        c = self._make_sale(payer="Hazem Junior")
        Sale.objects.filter(pk=c.pk).update(reference_number="0599999999")

        url = reverse("sales:employee_recent_sales")
        # Match by partial reference number → only the first row.
        resp = self.client.get(url, {"q": "0111"})
        self.assertContains(resp, "Hazem Salah")
        self.assertNotContains(resp, "Other Person")
        self.assertNotContains(resp, "Hazem Junior")

        # Match by payer name fragment → both Hazem rows, no Other.
        resp = self.client.get(url, {"q": "Hazem"})
        self.assertContains(resp, "Hazem Salah")
        self.assertContains(resp, "Hazem Junior")
        self.assertNotContains(resp, "Other Person")

    def test_payment_method_filter_excludes_other_methods(self):
        cash_sale = self._make_sale(payer="CashGuy")
        card_sale = create_sale(
            company=self.company,
            product=self.product,
            reference_number="0590000333",
            payer_name="CardGuy",
            payment_method=self.pm2,
            sell_price_actual=Decimal("10"),
            notes="",
            user=self.emp,
        )
        url = reverse("sales:employee_recent_sales")
        resp = self.client.get(url, {"payment_method": self.pm2.pk})
        self.assertContains(resp, "CardGuy")
        self.assertNotContains(resp, "CashGuy")

    def test_company_filter_excludes_other_companies(self):
        other_company = Company.objects.create(
            name="OtherCo",
            opening_balance=Decimal("500"),
            current_balance=Decimal("500"),
        )
        other_line = ProductLine.objects.create(company=other_company, name="OL")
        other_product = Product.objects.create(
            line=other_line,
            variant_label="OPkg",
            cost_price=Decimal("5"),
            default_sell_price=Decimal("10"),
        )
        self._make_sale(payer="MainCoPayer")
        create_sale(
            company=other_company,
            product=other_product,
            reference_number="0590000444",
            payer_name="OtherCoPayer",
            payment_method=self.pm,
            sell_price_actual=Decimal("10"),
            notes="",
            user=self.emp,
        )
        url = reverse("sales:employee_recent_sales")
        resp = self.client.get(url, {"company": other_company.pk})
        self.assertContains(resp, "OtherCoPayer")
        self.assertNotContains(resp, "MainCoPayer")

    def test_status_filter_releases_today_only_default(self):
        # A non-date filter should drop the "default to today" guard so
        # the employee can search across all of their history.
        from datetime import timedelta

        from django.utils import timezone

        old = self._make_sale(payer="OldStatusGuy")
        old.status = Sale.Status.PAID
        old.save(update_fields=["status"])
        Sale.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=5)
        )
        # A pending one today, which must NOT match a status=paid filter.
        self._make_sale(payer="TodayPending")

        url = reverse("sales:employee_recent_sales")
        resp = self.client.get(url, {"status": Sale.Status.PAID})
        self.assertContains(resp, "OldStatusGuy")
        self.assertNotContains(resp, "TodayPending")

    def test_text_search_ignores_other_employees(self):
        # Even if the search would otherwise match, a colleague's row
        # must never leak into the current employee's listing.
        s = self._make_sale(user=self.other, payer="ForeignHazem")
        Sale.objects.filter(pk=s.pk).update(reference_number="0590999111")
        url = reverse("sales:employee_recent_sales")
        resp = self.client.get(url, {"q": "Hazem"})
        self.assertNotContains(resp, "ForeignHazem")

    def test_filter_card_collapsed_by_default_with_no_active_badge(self):
        resp = self.client.get(reverse("sales:employee_recent_sales"))
        self.assertEqual(resp.status_code, 200)
        # Filter card uses <details> without `open`, so the search/filter UI
        # is collapsed until the user clicks the summary.
        self.assertContains(resp, "<details")
        self.assertNotContains(resp, "<details open")
        # No active filter badge when on the default (today) view.
        self.assertContains(resp, 'data-rd-filter-count style="display:none"')


class SalesCsvExportTests(TestCase):
    """End-to-end checks for the CSV downloads on management pages.

    The export views must (a) be guarded behind management auth, (b)
    honour the same ``q`` and ``status`` filters as the HTML page, and
    (c) emit a UTF-8 BOM so Excel opens Arabic correctly.
    """

    @classmethod
    def setUpTestData(cls):
        from accounts.models import UserProfile

        cls.boss = User.objects.create_user("csv_boss", password="x")
        UserProfile.objects.update_or_create(
            user=cls.boss,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        cls.worker = User.objects.create_user("csv_worker", password="x")
        UserProfile.objects.update_or_create(
            user=cls.worker,
            defaults={"role": UserProfile.Role.EMPLOYEE, "is_active_profile": True},
        )

        cls.company = Company.objects.create(name="جوال")
        cls.line = ProductLine.objects.create(company=cls.company, name="019")
        cls.product = Product.objects.create(
            line=cls.line,
            variant_label="100GB",
            cost_price=Decimal("8"),
            default_sell_price=Decimal("10"),
        )
        cls.pm = PaymentMethod.objects.create(name="نقدًا")

        def _mk(ref, payer, status=Sale.Status.PAID):
            return Sale.objects.create(
                company=cls.company,
                product=cls.product,
                reference_number=ref,
                payer_name=payer,
                payment_method=cls.pm,
                sell_price_actual=Decimal("10"),
                cost_price_snapshot=Decimal("8"),
                profit_snapshot=Decimal("2"),
                loss_snapshot=Decimal("0"),
                status=status,
                created_by=cls.boss,
            )

        cls.sale_paid = _mk("0590000111", "أحمد")
        cls.sale_pending = _mk("0590000222", "محمد", status=Sale.Status.PENDING)

    def test_employee_cannot_download_export(self):
        c = Client()
        c.force_login(self.worker)
        r = c.get(reverse("sales:sales_export_csv"))
        self.assertEqual(r.status_code, 302)

    def test_management_download_includes_bom_and_arabic(self):
        c = Client()
        c.force_login(self.boss)
        r = c.get(reverse("sales:sales_export_csv"))
        self.assertEqual(r.status_code, 200)
        body = b"".join(r.streaming_content).decode("utf-8")
        self.assertTrue(body.startswith("\ufeff"), "CSV must start with BOM for Excel")
        self.assertIn("جوال", body)
        self.assertIn("0590000111", body)
        self.assertIn("0590000222", body)
        self.assertIn("text/csv", r["Content-Type"])
        self.assertIn("attachment", r["Content-Disposition"])

    def test_status_filter_narrows_export(self):
        c = Client()
        c.force_login(self.boss)
        r = c.get(reverse("sales:sales_export_csv"), {"status": Sale.Status.PENDING})
        body = b"".join(r.streaming_content).decode("utf-8")
        self.assertIn("0590000222", body)
        self.assertNotIn("0590000111", body)

    def test_pending_payments_export_only_cash_pending(self):
        c = Client()
        c.force_login(self.boss)
        r = c.get(reverse("sales:pending_payments_export_csv"))
        body = b"".join(r.streaming_content).decode("utf-8")
        self.assertIn("0590000222", body)
        self.assertNotIn("0590000111", body)


class HxBoostMarkupTests(TestCase):
    """Sort headers and pagination links must update the table in place via
    HTMX rather than triggering a full page reload (which lost scroll
    position and felt jarring on every column-sort click).

    The pattern used everywhere is hx-boost on the results container,
    targeting itself with innerHTML swap. Inline action <a> links inside
    rows opt-out via hx-boost="false" so they still navigate normally.
    """

    @classmethod
    def setUpTestData(cls):
        from accounts.models import UserProfile

        cls.boss = User.objects.create_user("hxb_boss", password="x")
        UserProfile.objects.update_or_create(
            user=cls.boss,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.boss)

    def test_management_sale_list_results_container_is_boosted(self):
        r = self.client.get(reverse("sales:management_sale_list"))
        self.assertContains(r, 'id="sale-list-results"')
        self.assertContains(r, 'hx-target="#sale-list-results"')
        self.assertContains(r, 'hx-boost="true"')

    def test_pending_payments_results_container_is_boosted(self):
        r = self.client.get(reverse("sales:pending_payments"))
        self.assertContains(r, 'id="pending-payments-results"')
        self.assertContains(r, 'hx-target="#pending-payments-results"')
        self.assertContains(r, 'hx-boost="true"')

    def test_awaiting_approvals_results_container_is_boosted(self):
        r = self.client.get(reverse("sales:awaiting_approvals"))
        self.assertContains(r, 'id="awaiting-results"')
        self.assertContains(r, 'hx-target="#awaiting-results"')
        self.assertContains(r, 'hx-boost="true"')


class EmployeeRefreshPhoneTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from accounts.models import UserProfile

        cls.company = Company.objects.create(
            name="Sky Emp Co",
            opening_balance=Decimal("1000"),
            current_balance=Decimal("1000"),
        )
        cls.line = ProductLine.objects.create(company=cls.company, name="Line")
        cls.product = Product.objects.create(
            line=cls.line,
            variant_label="Pkg",
            cost_price=Decimal("5"),
            default_sell_price=Decimal("10"),
        )
        cls.pm = PaymentMethod.objects.create(name="Cash")
        cls.emp = User.objects.create_user("emp_refresh", password="x")
        UserProfile.objects.update_or_create(
            user=cls.emp,
            defaults={"role": UserProfile.Role.EMPLOYEE, "is_active_profile": True},
        )

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.emp)
        self.url = reverse("sales:employee_refresh_phone")

    def _csrf_post(self, data):
        page = self.client.get(reverse("sales:employee_entry"))
        token = str(page.context["csrf_token"])
        return self.client.post(
            self.url,
            {**data, "csrfmiddlewaretoken": token},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )

    def test_entry_page_shows_refresh_button_and_modal(self):
        resp = self.client.get(reverse("sales:employee_entry"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "rdEmployeeRefreshModal")
        self.assertContains(resp, reverse("sales:employee_refresh_phone"))

    def test_unregistered_phone_uses_public_refresh_flow(self):
        from phone_refresh.models import RefreshStatus

        status = RefreshStatus.objects.get(code="not_found")
        with patch("sales.views.employee.refresh_phone") as refresh_mock:
            refresh_mock.return_value = type(
                "R",
                (),
                {
                    "status": status,
                    "message_title": "رقم غير موجود",
                    "message_body": "لم يتم العثور على رقمك في النظام.",
                    "last_refresh_at": None,
                    "seconds_since_last_refresh": None,
                },
            )()
            resp = self._csrf_post({"phone": "0509999999"})
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["status"], "not_found")
        self.assertEqual(payload["message"]["body"], "لم يتم العثور على رقمك في النظام.")

    @patch("sales.views.employee.refresh_phone")
    def test_invalid_prefix_delegates_to_refresh_phone(self, refresh_mock):
        from phone_refresh.models import RefreshStatus

        status = RefreshStatus.objects.get(code="not_found")
        refresh_mock.return_value = type(
            "R",
            (),
            {
                "status": status,
                "message_title": "رقم غير موجود",
                "message_body": "لم يتم العثور على رقمك في النظام.",
                "last_refresh_at": None,
                "seconds_since_last_refresh": None,
            },
        )()
        resp = self._csrf_post({"phone": "0591234567"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "not_found")
        refresh_mock.assert_called_once()

    @patch("sales.views.employee.refresh_phone")
    def test_refreshes_registered_phone(self, refresh_mock):
        from phone_refresh.models import RefreshStatus

        create_sale(
            company=self.company,
            product=self.product,
            reference_number="0501234567",
            payer_name="Ali",
            payment_method=self.pm,
            sell_price_actual=Decimal("10"),
            notes="",
            user=self.emp,
        )
        status = RefreshStatus.objects.get(code="refreshed")
        refresh_mock.return_value = type(
            "R",
            (),
            {
                "status": status,
                "message_title": "تم",
                "message_body": "تم التحديث",
                "last_refresh_at": None,
                "seconds_since_last_refresh": None,
            },
        )()
        resp = self._csrf_post({"phone": "0501234567"})
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["status"], "refreshed")
        self.assertEqual(payload["message"]["body"], "تم التحديث")
        refresh_mock.assert_called_once()
        _args, kwargs = refresh_mock.call_args
        self.assertEqual(kwargs.get("source"), "employee")
