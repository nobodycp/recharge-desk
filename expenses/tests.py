from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import UserProfile
from expenses.models import Expense

User = get_user_model()


def _d(v):
    return Decimal(str(v))


class ExpenseDeleteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("mgmt_del", password="secret")
        p = cls.user.profile
        p.role = UserProfile.Role.MANAGEMENT
        p.save(update_fields=["role"])

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.user)
        self.expense = Expense.objects.create(
            title="To remove",
            category="test",
            amount=Decimal("1.00"),
            date=date(2026, 1, 10),
            created_by=self.user,
        )

    def test_delete_removes_expense(self):
        url = reverse("expenses:expense_delete", args=[self.expense.pk])
        r = self.client.get(reverse("expenses:expense_list"))
        token = r.context["csrf_token"]
        r = self.client.post(
            url,
            {"next": reverse("expenses:expense_list"), "csrfmiddlewaretoken": str(token)},
        )
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Expense.objects.filter(pk=self.expense.pk).exists())

    def test_delete_requires_post(self):
        url = reverse("expenses:expense_delete", args=[self.expense.pk])
        r = self.client.get(url)
        self.assertEqual(r.status_code, 405)
        self.assertTrue(Expense.objects.filter(pk=self.expense.pk).exists())


class ExpenseListTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("mgr_xp_list", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.user)
        today = date(2026, 4, 19)
        self.rent = Expense.objects.create(
            title="Office rent", category="ops", amount=_d(500),
            date=today, created_by=self.user,
        )
        self.utilities = Expense.objects.create(
            title="Internet", category="utilities", amount=_d(200),
            date=today - timedelta(days=2), created_by=self.user,
        )

    def test_list_renders_all(self):
        r = self.client.get(reverse("expenses:expense_list"))
        self.assertEqual(r.status_code, 200)
        rows = list(r.context["page_obj"].object_list)
        self.assertEqual(len(rows), 2)

    def test_list_filters_by_query(self):
        r = self.client.get(reverse("expenses:expense_list"), {"q": "rent"})
        rows = list(r.context["page_obj"].object_list)
        self.assertEqual(rows, [self.rent])

    def test_list_filters_by_date_range(self):
        r = self.client.get(
            reverse("expenses:expense_list"),
            {"date_from": "2026-04-19", "date_to": "2026-04-19"},
        )
        rows = list(r.context["page_obj"].object_list)
        self.assertEqual(rows, [self.rent])

    def test_list_htmx_returns_partial(self):
        r = self.client.get(
            reverse("expenses:expense_list"), HTTP_HX_REQUEST="true"
        )
        self.assertEqual(r.status_code, 200)
        templates = [t.name for t in r.templates if t.name]
        self.assertIn("expenses/partials/expense_list_results.html", templates)
        self.assertNotIn("expenses/expense_list.html", templates)

    def test_results_container_is_hx_boosted(self):
        """Sort headers and pagination links update the table in place via
        HTMX rather than triggering a full page reload."""
        r = self.client.get(reverse("expenses:expense_list"))
        self.assertContains(r, 'id="expense-list-results"')
        self.assertContains(r, 'hx-target="#expense-list-results"')
        self.assertContains(r, 'hx-boost="true"')


class ExpenseReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("mgr_xp_rep", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.user)

    def test_empty_report_renders(self):
        r = self.client.get(reverse("expenses:expense_report"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["total"] or 0, 0)
        self.assertEqual(r.context["by_category_rows"], [])
        self.assertEqual(r.context["page_obj"].paginator.count, 0)

    def test_report_aggregates_by_category(self):
        today = date(2026, 4, 19)
        Expense.objects.create(
            title="rent", category="ops", amount=_d(500),
            date=today, created_by=self.user,
        )
        Expense.objects.create(
            title="electric", category="utilities", amount=_d(200),
            date=today, created_by=self.user,
        )
        Expense.objects.create(
            title="water", category="utilities", amount=_d(50),
            date=today, created_by=self.user,
        )
        r = self.client.get(reverse("expenses:expense_report"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["total"], _d(750))
        rows = dict(r.context["by_category_rows"])
        self.assertEqual(rows["ops"], _d(500))
        self.assertEqual(rows["utilities"], _d(250))

    def test_report_date_filter_excludes_out_of_range(self):
        Expense.objects.create(
            title="old", category="ops", amount=_d(100),
            date=date(2026, 1, 1), created_by=self.user,
        )
        Expense.objects.create(
            title="new", category="ops", amount=_d(50),
            date=date(2026, 4, 19), created_by=self.user,
        )
        r = self.client.get(
            reverse("expenses:expense_report"),
            {"date_from": "2026-04-01", "date_to": "2026-04-30"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["total"], _d(50))
        rows = list(r.context["page_obj"].object_list)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].title, "new")

    def test_report_paginates_long_list(self):
        for i in range(60):
            Expense.objects.create(
                title=f"e{i}", category="ops", amount=_d(1),
                date=date(2026, 4, 1) + timedelta(days=i % 28),
                created_by=self.user,
            )
        r = self.client.get(reverse("expenses:expense_report"))
        self.assertEqual(r.status_code, 200)
        # Aggregates remain on the *full* filtered queryset.
        self.assertEqual(r.context["total"], _d(60))
        # But only the first page is materialized — paginator default is 25.
        self.assertEqual(r.context["page_obj"].paginator.count, 60)
        self.assertLess(len(list(r.context["page_obj"].object_list)), 60)


class ExpenseCreateAndEditTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("mgr_xp_crud", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.user)

    def test_create_persists_and_records_creator(self):
        r = self.client.post(
            reverse("expenses:expense_create"),
            {
                "title": "stamps",
                "category": "office",
                "amount": "12.50",
                "date": "2026-04-19",
                "notes": "",
            },
        )
        self.assertEqual(r.status_code, 302)
        e = Expense.objects.get(title="stamps")
        self.assertEqual(e.amount, _d("12.50"))
        self.assertEqual(e.created_by, self.user)

    def test_edit_updates_amount(self):
        e = Expense.objects.create(
            title="orig", category="ops", amount=_d(10),
            date=date(2026, 4, 19), created_by=self.user,
        )
        r = self.client.post(
            reverse("expenses:expense_edit", args=[e.pk]),
            {
                "title": "orig",
                "category": "ops",
                "amount": "20",
                "date": "2026-04-19",
                "notes": "",
            },
        )
        self.assertEqual(r.status_code, 302)
        e.refresh_from_db()
        self.assertEqual(e.amount, _d(20))


class ExpensePageKitTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("mgr_xp_kit", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.user)
        self.expense = Expense.objects.create(
            title="Kit rent", category="ops", amount=_d(500),
            date=date(2026, 4, 19), created_by=self.user,
        )

    def test_expense_report_form_lives_in_forms(self):
        from expenses.forms import ExpenseReportFilterForm

        form = ExpenseReportFilterForm({"date_from": "2026-04-01", "date_to": "2026-04-30"})
        self.assertTrue(form.is_valid())
        self.assertEqual(list(form.fields), ["date_from", "date_to"])
        r = self.client.get(reverse("expenses:expense_report"))
        self.assertIsInstance(r.context["form"], ExpenseReportFilterForm)

    def test_expense_filter_labels_without_colon(self):
        r = self.client.get(reverse("expenses:expense_list"))
        html = r.content.decode()
        self.assertNotIn(":</label>", html)
        self.assertIn('class="form-label"', html)
        self.assertIn("rd-filter-grid", html)
        self.assertNotIn("btn btn-primary\" type=\"submit\"", html)  # kit button is btn-sm

    def test_list_pages_use_page_kit(self):
        r = self.client.get(reverse("expenses:expense_list"))
        self.assertContains(r, 'class="rd-page"')
        self.assertContains(r, "rd-page-head")
        self.assertContains(r, "rd-icon-btn")
        self.assertContains(r, "rd-badge--neutral")
        self.assertContains(r, "kpi-expense")

    def test_expense_form_uses_form_kit(self):
        r = self.client.get(reverse("expenses:expense_create"))
        self.assertContains(r, "rd-page--form")
        self.assertContains(r, "rd-form-actions")
        self.assertNotContains(r, "app-card")
        self.assertNotContains(r, "max-width")
        r = self.client.post(reverse("expenses:expense_create"), {"title": "", "amount": "x"})
        self.assertContains(r, "is-invalid")
        self.assertContains(r, 'aria-invalid="true"')

    def test_report_keeps_edit_delete_and_has_mobile_cards(self):
        r = self.client.get(reverse("expenses:expense_report"))
        self.assertContains(r, reverse("expenses:expense_edit", args=[self.expense.pk]))
        self.assertContains(r, reverse("expenses:expense_delete", args=[self.expense.pk]))
        self.assertContains(r, "rd-mobile-row-card")
        self.assertContains(r, "rd-report-filter")
        self.assertContains(r, "rd-page-head")

    def test_report_empty_uses_empty_row(self):
        Expense.objects.all().delete()
        r = self.client.get(reverse("expenses:expense_report"))
        self.assertContains(r, "rd-empty-row")
