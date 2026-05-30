from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from django.test.utils import CaptureQueriesContext

from companies.layan_reconcile import norm_phone, reconcile_layan_report
from companies.models import Company, Product, ProductLine
from io import BytesIO
from datetime import date

User = get_user_model()


def _decimal(v):
    return Decimal(str(v))


class CompanyListQueryCountTests(TestCase):
    """Regression: company_list.html and product_list.html call
    ``{% if c.is_deletable %}`` on every row. The property used to fire
    its own EXISTS query per row — annotating ``has_sales_annotated``
    on the queryset collapses that into a single SQL statement."""

    @classmethod
    def setUpTestData(cls):
        from accounts.models import UserProfile

        cls.user = User.objects.create_user("mgr", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={
                "role": UserProfile.Role.MANAGEMENT,
                "is_active_profile": True,
            },
        )
        for i in range(6):
            company = Company.objects.create(
                name=f"Co{i}",
                opening_balance=_decimal(0),
                current_balance=_decimal(0),
            )
            line = ProductLine.objects.create(company=company, name=f"L{i}")
            Product.objects.create(
                line=line,
                variant_label=f"P{i}",
                cost_price=_decimal(5),
                default_sell_price=_decimal(50),
            )

    def test_company_list_does_not_run_per_row_exists_query(self):
        self.client.force_login(self.user)
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get("/management/companies/")
        self.assertEqual(resp.status_code, 200)
        # Without the annotation: 1 list query + 6 EXISTS = 7 just for
        # the rows. With the annotation the EXISTS is folded into the
        # list query. Add some slack for auth, session, paginator count.
        self.assertLess(
            len(ctx.captured_queries),
            12,
            f"company_list issued {len(ctx.captured_queries)} queries; "
            "is_deletable probably went back to per-row EXISTS.",
        )

    def test_product_list_does_not_run_per_row_exists_query(self):
        self.client.force_login(self.user)
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get("/management/products/")
        self.assertEqual(resp.status_code, 200)
        # 6 product lines + 6 variants × 2 EXISTS each = 12 extra
        # queries before the annotation. After: zero per-row EXISTS.
        self.assertLess(
            len(ctx.captured_queries),
            12,
            f"product_list issued {len(ctx.captured_queries)} queries; "
            "ProductLine.is_deletable / Product.is_deletable probably "
            "regressed to per-row EXISTS.",
        )

    def test_is_deletable_property_still_works_without_annotation(self):
        """When called outside a list view (e.g. in a detail page) the
        property must keep firing its own EXISTS — it should not be
        broken by the new annotation shortcut."""
        company = Company.objects.first()
        # No annotation in the round-trip — fall back to the EXISTS query.
        fresh = Company.objects.get(pk=company.pk)
        self.assertTrue(fresh.is_deletable)

    def test_company_detail_renders_tabs(self):
        self.client.force_login(self.user)
        company = Company.objects.first()
        r = self.client.get(
            reverse("companies:company_detail", args=[company.pk])
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "rd-section-tabs")
        self.assertContains(r, "Manual balance top-up")
        self.assertContains(r, "company-sales-grid")
        self.assertContains(r, "company-ledger-grid")
        r2 = self.client.get(
            reverse("companies:company_detail", args=[company.pk]),
            {"tab": "sales"},
        )
        self.assertNotContains(r2, "Manual balance top-up")
        self.assertContains(r2, "company-sales-grid")

    def test_company_list_results_container_is_hx_boosted(self):
        """Sort headers and pagination links update the table in place via
        HTMX rather than triggering a full page reload."""
        self.client.force_login(self.user)
        r = self.client.get("/management/companies/")
        self.assertContains(r, 'id="company-list-results"')
        self.assertContains(r, 'hx-target="#company-list-results"')
        self.assertContains(r, 'hx-boost="true"')


class LayanReconcileTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from accounts.models import UserProfile

        cls.user = User.objects.create_user("layan_mgr", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={
                "role": UserProfile.Role.MANAGEMENT,
                "is_active_profile": True,
            },
        )
        cls.company = Company.objects.create(
            name="Layan Test",
            phone_refresh_provider="layan",
            opening_balance=_decimal(1000),
            current_balance=_decimal(1000),
        )

    def test_norm_phone(self):
        self.assertEqual(norm_phone("512345678"), "0512345678")
        self.assertEqual(norm_phone("972512345678"), "0512345678")

    def _minimal_workbook(self, rows):
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["h1", "h2"])
        ws.append(["h1", "h2"])
        for raw, op, cost, bal_before, bal_after, dt in rows:
            # 0=phone, 3=op, 7=before, 8=after, 10=cost, 12=date
            row = [raw, None, None, op, None, None, None, bal_before, bal_after, None, cost, None, dt]
            ws.append(row)
        buf = BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf

    def test_reconcile_not_recorded_and_split(self):
        from sales.models import PaymentMethod, Sale

        buf = self._minimal_workbook(
            [
                ("0511111111", "تفعيل", 30, 1000, 970, "01/05/2026 10:00"),
                ("0522222222", "تفعيل", 30, 970, 940, "02/05/2026 10:00"),
                ("0522222222", "إعادة مال", -29, 940, 941, "03/05/2026 10:00"),
            ]
        )
        product = Product.objects.create(
            line=ProductLine.objects.create(company=self.company, name="L"),
            variant_label="v",
            cost_price=_decimal(30),
            default_sell_price=_decimal(50),
        )
        pm = PaymentMethod.objects.create(name="cash")
        Sale.objects.create(
            company=self.company,
            product=product,
            reference_number="0533333333",
            payer_name="test",
            payment_method=pm,
            cost_price_snapshot=_decimal(30),
            sell_price_actual=_decimal(50),
            profit_snapshot=_decimal(20),
            loss_snapshot=_decimal(0),
            status=Sale.Status.PAID,
            created_by=self.user,
        )
        result = reconcile_layan_report(
            self.company,
            buf,
            period_from=date(2026, 5, 1),
            period_to=date(2026, 5, 31),
        )
        self.assertEqual(len(result.not_recorded), 1)
        self.assertEqual(result.not_recorded[0].phone, "0511111111")
        self.assertEqual(result.total_not_recorded, _decimal(30))
        self.assertEqual(len(result.split_settlements), 1)
        self.assertEqual(result.split_settlements[0].phone, "0522222222")
        self.assertEqual(result.estimated_deficit, _decimal(31))

    def test_disconnect_with_sale_goes_to_split_not_mismatch(self):
        buf = self._minimal_workbook(
            [
                ("0535768111", "تفعيل", 70, 1000, 930, "14/05/2026 10:00"),
                ("0535768111", "إعادة مال", -33.83, 930, 934, "16/05/2026 10:00"),
            ]
        )
        product = Product.objects.create(
            line=ProductLine.objects.create(company=self.company, name="L2"),
            variant_label="v2",
            cost_price=_decimal(45),
            default_sell_price=_decimal(50),
        )
        from sales.models import PaymentMethod, Sale

        pm = PaymentMethod.objects.create(name="cash4")
        Sale.objects.create(
            company=self.company,
            product=product,
            reference_number="0535768111",
            payer_name="test",
            payment_method=pm,
            cost_price_snapshot=_decimal(45),
            sell_price_actual=_decimal(50),
            profit_snapshot=_decimal(5),
            loss_snapshot=_decimal(0),
            status=Sale.Status.PAID,
            created_by=self.user,
        )
        result = reconcile_layan_report(
            self.company,
            buf,
            period_from=date(2026, 5, 1),
            period_to=date(2026, 5, 31),
        )
        self.assertEqual(len(result.split_settlements), 1)
        self.assertEqual(result.split_settlements[0].phone, "0535768111")
        self.assertEqual(len(result.amount_mismatches), 0)

    def test_skips_reactivation_when_balance_unchanged(self):
        buf = self._minimal_workbook(
            [
                (
                    "0512816409",
                    "تفعيل خط هاتف جديد - We",
                    60,
                    500,
                    500,
                    "10/05/2026 10:00",
                ),
                ("0512816409", "تفعيل", 30, 500, 470, "11/05/2026 10:00"),
            ]
        )
        product = Product.objects.create(
            line=ProductLine.objects.create(company=self.company, name="L3"),
            variant_label="v3",
            cost_price=_decimal(30),
            default_sell_price=_decimal(50),
        )
        from sales.models import PaymentMethod, Sale

        pm = PaymentMethod.objects.create(name="cash5")
        Sale.objects.create(
            company=self.company,
            product=product,
            reference_number="0512816409",
            payer_name="test",
            payment_method=pm,
            cost_price_snapshot=_decimal(30),
            sell_price_actual=_decimal(50),
            profit_snapshot=_decimal(20),
            loss_snapshot=_decimal(0),
            status=Sale.Status.PAID,
            created_by=self.user,
        )
        result = reconcile_layan_report(
            self.company,
            buf,
            period_from=date(2026, 5, 1),
            period_to=date(2026, 5, 31),
        )
        row = next(r for r in result.matched if r.phone == "0512816409")
        self.assertEqual(row.layan_net, _decimal(30))
        self.assertEqual(len(result.amount_mismatches), 0)

    def test_reconcile_finds_sale_under_other_supplier(self):
        sky = Company.objects.create(
            name="Sky",
            phone_refresh_provider="sky",
            opening_balance=_decimal(0),
            current_balance=_decimal(0),
        )
        buf = self._minimal_workbook(
            [("0535767941", "تفعيل", 30, 1000, 970, "14/05/2026 10:00")],
        )
        product = Product.objects.create(
            line=ProductLine.objects.create(company=sky, name="L"),
            variant_label="v",
            cost_price=_decimal(45),
            default_sell_price=_decimal(50),
        )
        from sales.models import PaymentMethod, Sale

        pm = PaymentMethod.objects.create(name="cash2")
        Sale.objects.create(
            company=sky,
            product=product,
            reference_number="0535767941",
            payer_name="test",
            payment_method=pm,
            cost_price_snapshot=_decimal(45),
            sell_price_actual=_decimal(50),
            profit_snapshot=_decimal(5),
            loss_snapshot=_decimal(0),
            status=Sale.Status.PAID,
            created_by=self.user,
        )
        result = reconcile_layan_report(
            self.company,
            buf,
            period_from=date(2026, 5, 1),
            period_to=date(2026, 5, 31),
        )
        self.assertEqual(len(result.not_recorded), 0)
        self.assertEqual(len(result.amount_mismatches), 0)
        self.assertEqual(len(result.logged_other_supplier), 1)
        self.assertEqual(result.logged_other_supplier[0].phone, "0535767941")
        self.assertEqual(result.logged_other_supplier[0].rd_suppliers, "Sky")

    def test_rd_only_lists_layan_sales_not_other_suppliers(self):
        sky = Company.objects.create(name="Sky", opening_balance=_decimal(0))
        product = Product.objects.create(
            line=ProductLine.objects.create(company=sky, name="L"),
            variant_label="v",
            cost_price=_decimal(30),
            default_sell_price=_decimal(50),
        )
        from sales.models import PaymentMethod, Sale

        pm = PaymentMethod.objects.create(name="cash3")
        Sale.objects.create(
            company=sky,
            product=product,
            reference_number="0599999999",
            payer_name="test",
            payment_method=pm,
            cost_price_snapshot=_decimal(30),
            sell_price_actual=_decimal(50),
            profit_snapshot=_decimal(20),
            loss_snapshot=_decimal(0),
            status=Sale.Status.PAID,
            created_by=self.user,
        )
        buf = self._minimal_workbook([])
        result = reconcile_layan_report(
            self.company,
            buf,
            period_from=date(2026, 5, 1),
            period_to=date(2026, 5, 31),
        )
        self.assertEqual(len(result.rd_only), 0)

    def test_layan_reconcile_view_requires_layan(self):
        other = Company.objects.create(name="Other Co", opening_balance=_decimal(0))
        self.client.force_login(self.user)
        r = self.client.get(reverse("companies:layan_reconcile", args=[other.pk]))
        self.assertEqual(r.status_code, 302)

    def test_layan_reconcile_page_renders(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse("companies:layan_reconcile", args=[self.company.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Run matching")
