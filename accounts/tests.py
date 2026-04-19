"""Permission-matrix tests for the whole project.

Walks the canonical management / employee / public URLs and confirms each
view has the right gate. The goal is to catch regressions where a new
view is added without the proper @management_required / @employee_required
decorator, or where an existing view loses its decorator in a refactor.

Decorator behavior recap (accounts/permissions.py):

* anonymous user -> redirect to LOGIN_URL (302).
* authenticated but wrong role -> redirect to "core:forbidden" (302).
* right role -> view runs (typically 200, or 405 for POST-only views
  hit via GET).
"""

from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import UserProfile
from companies.models import Company, Product, ProductLine
from customers.models import Customer
from sales.models import PaymentMethod

User = get_user_model()


def _decimal(v):
    return Decimal(str(v))


class PermissionMatrixTests(TestCase):
    """End-to-end check that every gated URL routes the wrong role away."""

    @classmethod
    def setUpTestData(cls):
        cls.management = User.objects.create_user("mgr_perm", password="x")
        UserProfile.objects.update_or_create(
            user=cls.management,
            defaults={
                "role": UserProfile.Role.MANAGEMENT,
                "is_active_profile": True,
            },
        )

        cls.employee = User.objects.create_user("emp_perm", password="x")
        UserProfile.objects.update_or_create(
            user=cls.employee,
            defaults={
                "role": UserProfile.Role.EMPLOYEE,
                "is_active_profile": True,
            },
        )

        # Stable PKs for URLs that require an ID.
        cls.company = Company.objects.create(
            name="PermCo",
            opening_balance=_decimal(1000),
            current_balance=_decimal(1000),
        )
        cls.line = ProductLine.objects.create(company=cls.company, name="L")
        cls.product = Product.objects.create(
            line=cls.line,
            variant_label="P",
            cost_price=_decimal(5),
            default_sell_price=_decimal(20),
        )
        cls.payment = PaymentMethod.objects.create(name="Cash")
        cls.customer = Customer.objects.create(name="Test", created_by=cls.management)

    # ---------------------------------------------------------------- helpers
    @property
    def _login_url(self):
        return str(settings.LOGIN_URL)

    def _is_forbidden_redirect(self, response):
        return (
            response.status_code == 302
            and reverse("core:forbidden") in response["Location"]
        )

    def _all_management_urls(self):
        """One representative URL per management view across every app."""
        c = self.company.pk
        cu = self.customer.pk
        pm = self.payment.pk
        prod = self.product.pk
        line = self.line.pk
        return [
            # reports
            reverse("reports:dashboard"),
            reverse("reports:profit_report"),
            reverse("reports:sales_report"),
            reverse("reports:company_report", args=[c]),
            # accounts
            reverse("accounts:user_list"),
            reverse("accounts:user_create"),
            # companies
            reverse("companies:company_list"),
            reverse("companies:company_create"),
            reverse("companies:company_edit", args=[c]),
            reverse("companies:product_list"),
            reverse("companies:product_line_create"),
            reverse("companies:product_line_edit", args=[line]),
            reverse("companies:product_variant_create", args=[line]),
            reverse("companies:product_variant_edit", args=[prod]),
            # customers
            reverse("customers:customer_list"),
            reverse("customers:customer_create"),
            reverse("customers:customer_detail", args=[cu]),
            reverse("customers:customer_edit", args=[cu]),
            # expenses
            reverse("expenses:expense_list"),
            reverse("expenses:expense_create"),
            reverse("expenses:expense_report"),
            # sales (management surface)
            reverse("sales:management_sale_list"),
            reverse("sales:pending_payments"),
            reverse("sales:awaiting_approvals"),
            reverse("sales:payment_method_list"),
            reverse("sales:payment_method_create"),
            reverse("sales:payment_method_edit", args=[pm]),
        ]

    def _all_employee_urls(self):
        return [
            reverse("sales:employee_entry"),
        ]

    # ------------------------------------------------------------------ anon
    def test_anonymous_user_redirected_to_login_for_every_management_url(self):
        client = Client()
        for url in self._all_management_urls():
            with self.subTest(url=url):
                r = client.get(url)
                self.assertTrue(
                    r.status_code == 302
                    and r["Location"].startswith(self._login_url),
                    f"{url} did not redirect anonymous to login (got {r.status_code} -> {r.get('Location')})",
                )

    def test_anonymous_user_redirected_to_login_for_employee_urls(self):
        client = Client()
        for url in self._all_employee_urls():
            with self.subTest(url=url):
                r = client.get(url)
                self.assertEqual(r.status_code, 302)
                self.assertTrue(r["Location"].startswith(self._login_url))

    # -------------------------------------------------------------- employee
    def test_employee_blocked_from_every_management_url(self):
        client = Client()
        client.force_login(self.employee)
        for url in self._all_management_urls():
            with self.subTest(url=url):
                r = client.get(url)
                self.assertTrue(
                    self._is_forbidden_redirect(r),
                    f"{url} let an employee through (got {r.status_code} -> {r.get('Location')})",
                )

    def test_employee_can_reach_employee_urls(self):
        client = Client()
        client.force_login(self.employee)
        for url in self._all_employee_urls():
            with self.subTest(url=url):
                r = client.get(url)
                self.assertEqual(r.status_code, 200, f"employee URL {url} returned {r.status_code}")

    # ------------------------------------------------------------ management
    def test_management_can_reach_every_management_url(self):
        client = Client()
        client.force_login(self.management)
        for url in self._all_management_urls():
            with self.subTest(url=url):
                r = client.get(url)
                # 200 = renders, 302 = post-action redirect — both are "passed the gate".
                self.assertIn(
                    r.status_code,
                    (200, 302),
                    f"{url} broke for management (got {r.status_code})",
                )

    def test_management_can_reach_employee_urls(self):
        """employee_required intentionally also lets management in."""
        client = Client()
        client.force_login(self.management)
        for url in self._all_employee_urls():
            with self.subTest(url=url):
                r = client.get(url)
                self.assertEqual(r.status_code, 200)

    # ---------------------------------------------------------- inactive role
    def test_management_with_inactive_profile_is_blocked(self):
        UserProfile.objects.filter(user=self.management).update(is_active_profile=False)
        client = Client()
        client.force_login(self.management)
        r = client.get(reverse("reports:dashboard"))
        self.assertTrue(self._is_forbidden_redirect(r))


class HomeRedirectTests(TestCase):
    """`core:home` should funnel each user to the right landing page."""

    @classmethod
    def setUpTestData(cls):
        cls.management = User.objects.create_user("mgr_home", password="x")
        UserProfile.objects.update_or_create(
            user=cls.management,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        cls.employee = User.objects.create_user("emp_home", password="x")
        UserProfile.objects.update_or_create(
            user=cls.employee,
            defaults={"role": UserProfile.Role.EMPLOYEE, "is_active_profile": True},
        )

    def test_anonymous_goes_to_login(self):
        r = Client().get(reverse("core:home"))
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("accounts:login"), r["Location"])

    def test_management_goes_to_dashboard(self):
        c = Client()
        c.force_login(self.management)
        r = c.get(reverse("core:home"))
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("reports:dashboard"), r["Location"])

    def test_employee_goes_to_entry(self):
        c = Client()
        c.force_login(self.employee)
        r = c.get(reverse("core:home"))
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("sales:employee_entry"), r["Location"])


class UserProfileSignalTests(TestCase):
    """post_save signal must auto-create a profile with the right default role."""

    def test_new_regular_user_gets_employee_profile(self):
        u = User.objects.create_user("auto_emp", password="x")
        self.assertTrue(hasattr(u, "profile"))
        self.assertEqual(u.profile.role, UserProfile.Role.EMPLOYEE)

    def test_new_superuser_gets_management_profile(self):
        u = User.objects.create_superuser("auto_super", "a@b.c", "x")
        self.assertEqual(u.profile.role, UserProfile.Role.MANAGEMENT)

    def test_signal_is_idempotent(self):
        u = User.objects.create_user("auto_emp2", password="x")
        u.first_name = "X"
        u.save()
        self.assertEqual(UserProfile.objects.filter(user=u).count(), 1)


class ForbiddenPageTests(TestCase):
    def test_forbidden_renders_403(self):
        r = Client().get(reverse("core:forbidden"))
        self.assertEqual(r.status_code, 403)
