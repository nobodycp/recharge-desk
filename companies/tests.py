from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from django.test.utils import CaptureQueriesContext

from companies.models import Company, Product, ProductLine

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
            # Distinctive names — short "L1" collides with substrings like
            # ``USE_L10N`` on DEBUG error/settings dumps in CI.
            line = ProductLine.objects.create(company=company, name=f"LineUnique{i}")
            Product.objects.create(
                line=line,
                variant_label=f"PkgUnique{i}",
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
        # Annotation folds is_deletable into the list/prefetch SELECTs
        # (EXISTS appears there as has_sales_annotated). A regression is a
        # standalone per-row EXISTS query outside those annotated SELECTs.
        per_row_exists = [
            q["sql"]
            for q in ctx.captured_queries
            if "EXISTS" in q["sql"].upper()
            and "sales_sale" in q["sql"]
            and "has_sales_annotated" not in q["sql"]
        ]
        self.assertEqual(
            per_row_exists,
            [],
            "ProductLine.is_deletable / Product.is_deletable probably "
            "regressed to per-row EXISTS.",
        )
        # Grouped by company; packages collapsed until a line is expanded.
        self.assertContains(resp, "rd-product-company")
        self.assertContains(resp, "rd-product-line")
        self.assertContains(resp, "rd-product-line__details")
        groups = resp.context["company_groups"]
        self.assertGreaterEqual(len(groups), 1)
        self.assertTrue(all("company" in g and "lines" in g for g in groups))

    def test_product_list_filters_by_company_and_search(self):
        self.client.force_login(self.user)
        company = Company.objects.get(name="Co0")

        by_company = self.client.get(
            "/management/products/", {"company": str(company.pk)}
        )
        self.assertEqual(by_company.status_code, 200)
        self.assertContains(by_company, "LineUnique0")
        self.assertNotContains(by_company, "LineUnique1")
        self.assertEqual(len(by_company.context["company_groups"]), 1)
        self.assertContains(by_company, 'name="company"')
        self.assertContains(by_company, 'name="q"')

        by_line = self.client.get("/management/products/", {"q": "LineUnique2"})
        self.assertContains(by_line, "LineUnique2")
        self.assertNotContains(by_line, "LineUnique1")
        # Searching expands matching package panels.
        self.assertContains(by_line, 'class="rd-product-line__details" open')

        by_package = self.client.get("/management/products/", {"q": "PkgUnique3"})
        self.assertContains(by_package, "LineUnique3")
        self.assertContains(by_package, "PkgUnique3")

    def test_archive_package_with_sales_hides_it_without_deleting_sales(self):
        from sales.models import PaymentMethod, Sale

        self.client.force_login(self.user)
        company = Company.objects.get(name="Co0")
        product = Product.objects.get(variant_label="PkgUnique0")
        pm = PaymentMethod.objects.create(name="CashArchive")
        sale = Sale.objects.create(
            company=company,
            product=product,
            reference_number="0599000111",
            payer_name="Archive Test",
            payment_method=pm,
            sell_price_actual=_decimal(50),
            cost_price_snapshot=_decimal(5),
            profit_snapshot=_decimal(45),
            loss_snapshot=_decimal(0),
            status=Sale.Status.PAID,
            created_by=self.user,
        )
        self.assertFalse(product.is_deletable)

        # Hard delete blocked.
        blocked = self.client.post(
            reverse("companies:product_variant_delete", args=[product.pk])
        )
        self.assertEqual(blocked.status_code, 302)
        self.assertTrue(Product.objects.filter(pk=product.pk).exists())

        archived = self.client.post(
            reverse("companies:product_variant_archive", args=[product.pk])
        )
        self.assertEqual(archived.status_code, 302)
        product.refresh_from_db()
        self.assertFalse(product.is_active)
        sale.refresh_from_db()
        self.assertEqual(sale.product_id, product.pk)

        # Hidden from default product list (ignore flash messages).
        listing = self.client.get("/management/products/")
        visible_labels = [
            v.variant_label
            for line in listing.context["lines"]
            for v in line.variants.all()
        ]
        self.assertNotIn("PkgUnique0", visible_labels)

        # Visible again when showing archived.
        with_archived = self.client.get(
            "/management/products/", {"show_archived": "1"}
        )
        archived_labels = [
            v.variant_label
            for line in with_archived.context["lines"]
            for v in line.variants.all()
        ]
        self.assertIn("PkgUnique0", archived_labels)
        self.assertContains(with_archived, "Archived")

        restored = self.client.post(
            reverse("companies:product_variant_unarchive", args=[product.pk])
        )
        self.assertEqual(restored.status_code, 302)
        product.refresh_from_db()
        self.assertTrue(product.is_active)

    def test_product_variant_form_omits_package_icon(self):
        from companies.forms import ProductVariantForm

        form = ProductVariantForm()
        self.assertNotIn("icon", form.fields)
        self.assertIn("variant_label", form.fields)

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
