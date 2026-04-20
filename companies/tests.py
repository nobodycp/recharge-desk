from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
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

    def test_company_list_results_container_is_hx_boosted(self):
        """Sort headers and pagination links update the table in place via
        HTMX rather than triggering a full page reload."""
        self.client.force_login(self.user)
        r = self.client.get("/management/companies/")
        self.assertContains(r, 'id="company-list-results"')
        self.assertContains(r, 'hx-target="#company-list-results"')
        self.assertContains(r, 'hx-boost="true"')
