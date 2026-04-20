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
            # core (management-only branding editor)
            reverse("core:site_branding"),
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


class AccountSettingsTests(TestCase):
    """Self-service profile / password page: open to every logged-in user."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("alice", password="oldpw1234x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={
                "role": UserProfile.Role.EMPLOYEE,
                "is_active_profile": True,
                "full_name": "Alice A.",
            },
        )

    def _login(self):
        c = Client()
        c.force_login(self.user)
        return c

    # ---- access ----------------------------------------------------------
    def test_anonymous_redirected_to_login(self):
        r = Client().get(reverse("accounts:account_settings"))
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("accounts:login"), r["Location"])

    def test_employee_can_open_their_settings(self):
        r = self._login().get(reverse("accounts:account_settings"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "alice")

    def test_management_can_also_open_settings(self):
        u = User.objects.create_user("bob", password="x")
        UserProfile.objects.update_or_create(
            user=u, defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True}
        )
        c = Client()
        c.force_login(u)
        r = c.get(reverse("accounts:account_settings"))
        self.assertEqual(r.status_code, 200)

    # ---- profile form ----------------------------------------------------
    def test_save_profile_updates_username_and_full_name(self):
        c = self._login()
        r = c.post(
            reverse("accounts:account_settings"),
            {"form": "profile", "username": "alice2", "full_name": "Alice II"},
        )
        self.assertEqual(r.status_code, 302)
        self.user.refresh_from_db()
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.username, "alice2")
        self.assertEqual(self.user.profile.full_name, "Alice II")

    def test_username_collision_is_rejected(self):
        User.objects.create_user("taken", password="x")
        c = self._login()
        r = c.post(
            reverse("accounts:account_settings"),
            {"form": "profile", "username": "taken", "full_name": "Alice"},
        )
        self.assertEqual(r.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "alice")

    # ---- password form ---------------------------------------------------
    def test_password_change_succeeds_and_keeps_session(self):
        c = self._login()
        r = c.post(
            reverse("accounts:account_settings"),
            {
                "form": "password",
                "old_password": "oldpw1234x",
                "new_password1": "freshPw9876!",
                "new_password2": "freshPw9876!",
            },
        )
        self.assertEqual(r.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("freshPw9876!"))
        # Same client (still authenticated thanks to update_session_auth_hash).
        r2 = c.get(reverse("accounts:account_settings"))
        self.assertEqual(r2.status_code, 200)

    def test_wrong_old_password_is_rejected(self):
        c = self._login()
        r = c.post(
            reverse("accounts:account_settings"),
            {
                "form": "password",
                "old_password": "WRONG",
                "new_password1": "freshPw9876!",
                "new_password2": "freshPw9876!",
            },
        )
        self.assertEqual(r.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("oldpw1234x"))


class UserMenuRenderTests(TestCase):
    """The dropdown partial must render in both base templates."""

    @classmethod
    def setUpTestData(cls):
        cls.management = User.objects.create_user("ceo", password="x")
        UserProfile.objects.update_or_create(
            user=cls.management,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True, "full_name": "Big Boss"},
        )
        cls.employee = User.objects.create_user("emp", password="x")
        UserProfile.objects.update_or_create(
            user=cls.employee,
            defaults={"role": UserProfile.Role.EMPLOYEE, "is_active_profile": True, "full_name": "Worker B."},
        )

    def test_menu_renders_on_management_topbar(self):
        c = Client()
        c.force_login(self.management)
        r = c.get(reverse("reports:dashboard"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "rd-user-menu")
        self.assertContains(r, reverse("accounts:account_settings"))
        self.assertContains(r, reverse("accounts:logout"))
        # Old plain logout button must be gone — the form now lives inside the panel.
        self.assertNotContains(r, "btn btn-sm btn-outline-secondary\" type=\"submit\"")

    def test_menu_renders_on_employee_topbar(self):
        c = Client()
        c.force_login(self.employee)
        r = c.get(reverse("sales:employee_entry"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "rd-user-menu")
        self.assertContains(r, reverse("accounts:account_settings"))


class ManagementUserEditTests(TestCase):
    """Management can edit every field of any user, including password."""

    @classmethod
    def setUpTestData(cls):
        cls.boss = User.objects.create_user("boss", password="bossPw1234")
        UserProfile.objects.update_or_create(
            user=cls.boss,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        # Second active management so we don't trip the "last admin" guard.
        cls.boss2 = User.objects.create_user("boss2", password="x")
        UserProfile.objects.update_or_create(
            user=cls.boss2,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        cls.target = User.objects.create_user("worker", password="oldPw1234")
        UserProfile.objects.update_or_create(
            user=cls.target,
            defaults={
                "role": UserProfile.Role.EMPLOYEE,
                "is_active_profile": True,
                "full_name": "Worker O.",
            },
        )

    def _login(self, user=None):
        c = Client()
        c.force_login(user or self.boss)
        return c

    def test_edit_updates_username_full_name_role_and_flags(self):
        c = self._login()
        url = reverse("accounts:user_edit", args=[self.target.profile.pk])
        r = c.post(
            url,
            {
                "username": "worker_renamed",
                "full_name": "Worker Renamed",
                "role": UserProfile.Role.MANAGEMENT,
                "is_active_profile": "on",
                # is_active intentionally omitted → unchecked → False
                "new_password1": "",
                "new_password2": "",
            },
        )
        self.assertEqual(r.status_code, 302)
        self.target.refresh_from_db()
        self.target.profile.refresh_from_db()
        self.assertEqual(self.target.username, "worker_renamed")
        self.assertEqual(self.target.profile.full_name, "Worker Renamed")
        self.assertEqual(self.target.profile.role, UserProfile.Role.MANAGEMENT)
        self.assertTrue(self.target.profile.is_active_profile)
        self.assertFalse(self.target.is_active)

    def test_edit_can_reset_password_when_provided(self):
        c = self._login()
        url = reverse("accounts:user_edit", args=[self.target.profile.pk])
        r = c.post(
            url,
            {
                "username": "worker",
                "full_name": "Worker O.",
                "role": UserProfile.Role.EMPLOYEE,
                "is_active_profile": "on",
                "is_active": "on",
                "new_password1": "brandNewPw99!",
                "new_password2": "brandNewPw99!",
            },
        )
        self.assertEqual(r.status_code, 302)
        self.target.refresh_from_db()
        self.assertTrue(self.target.check_password("brandNewPw99!"))

    def test_edit_keeps_password_when_left_blank(self):
        c = self._login()
        url = reverse("accounts:user_edit", args=[self.target.profile.pk])
        c.post(
            url,
            {
                "username": "worker",
                "full_name": "X",
                "role": UserProfile.Role.EMPLOYEE,
                "is_active_profile": "on",
                "is_active": "on",
                "new_password1": "",
                "new_password2": "",
            },
        )
        self.target.refresh_from_db()
        self.assertTrue(self.target.check_password("oldPw1234"))

    def test_password_mismatch_is_rejected(self):
        c = self._login()
        url = reverse("accounts:user_edit", args=[self.target.profile.pk])
        r = c.post(
            url,
            {
                "username": "worker",
                "full_name": "X",
                "role": UserProfile.Role.EMPLOYEE,
                "is_active_profile": "on",
                "is_active": "on",
                "new_password1": "matching1",
                "new_password2": "MISMATCHED",
            },
        )
        self.assertEqual(r.status_code, 200)
        self.target.refresh_from_db()
        self.assertTrue(self.target.check_password("oldPw1234"))


class ManagementUserDeleteTests(TestCase):
    """Hard-delete with safety guards (self / last admin / referenced rows)."""

    @classmethod
    def setUpTestData(cls):
        cls.boss = User.objects.create_user("boss", password="x")
        UserProfile.objects.update_or_create(
            user=cls.boss,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        cls.second_admin = User.objects.create_user("boss2", password="x")
        UserProfile.objects.update_or_create(
            user=cls.second_admin,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        cls.disposable = User.objects.create_user("disposable", password="x")
        UserProfile.objects.update_or_create(
            user=cls.disposable,
            defaults={"role": UserProfile.Role.EMPLOYEE, "is_active_profile": True},
        )

    def _login(self, user=None):
        c = Client()
        c.force_login(user or self.boss)
        return c

    def test_get_does_not_delete(self):
        url = reverse("accounts:user_delete", args=[self.disposable.profile.pk])
        r = self._login().get(url)
        self.assertEqual(r.status_code, 302)
        self.assertTrue(User.objects.filter(pk=self.disposable.pk).exists())

    def test_management_can_delete_unrelated_user(self):
        url = reverse("accounts:user_delete", args=[self.disposable.profile.pk])
        r = self._login().post(url)
        self.assertEqual(r.status_code, 302)
        self.assertFalse(User.objects.filter(pk=self.disposable.pk).exists())

    def test_cannot_delete_self(self):
        url = reverse("accounts:user_delete", args=[self.boss.profile.pk])
        r = self._login().post(url, follow=True)
        self.assertTrue(User.objects.filter(pk=self.boss.pk).exists())
        # The view bounces back to the edit page with an error in messages.
        self.assertContains(r, "your own account", status_code=200)

    def test_cannot_delete_last_active_management(self):
        # Set up a state where `boss` is the only MANAGEMENT-role active
        # profile. The actor is a superuser whose own profile is downgraded
        # so it doesn't count toward the management tally — superusers can
        # still reach management views (is_management() short-circuits on
        # is_superuser) but the safety guard tracks role-based admins.
        self.second_admin.profile.role = UserProfile.Role.EMPLOYEE
        self.second_admin.profile.save()
        super_actor = User.objects.create_superuser("rootadmin", "r@x.y", "x")
        super_actor.profile.role = UserProfile.Role.EMPLOYEE
        super_actor.profile.save()
        c = Client()
        c.force_login(super_actor)
        url = reverse("accounts:user_delete", args=[self.boss.profile.pk])
        r = c.post(url, follow=True)
        self.assertTrue(User.objects.filter(pk=self.boss.pk).exists())
        self.assertContains(r, "last active management", status_code=200)

    def test_protected_records_block_delete_with_friendly_message(self):
        # Wire `disposable` into a PROTECTed FK (Customer.created_by).
        Customer.objects.create(name="Owned", created_by=self.disposable)
        url = reverse("accounts:user_delete", args=[self.disposable.profile.pk])
        r = self._login().post(url, follow=True)
        self.assertTrue(User.objects.filter(pk=self.disposable.pk).exists())
        self.assertContains(r, "linked to existing", status_code=200)

    def test_employee_cannot_reach_delete_endpoint(self):
        emp = User.objects.create_user("emp_perm2", password="x")
        UserProfile.objects.update_or_create(
            user=emp,
            defaults={"role": UserProfile.Role.EMPLOYEE, "is_active_profile": True},
        )
        c = Client()
        c.force_login(emp)
        r = c.post(reverse("accounts:user_delete", args=[self.disposable.profile.pk]))
        # management_required redirects employees to the forbidden page.
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("core:forbidden"), r["Location"])
        self.assertTrue(User.objects.filter(pk=self.disposable.pk).exists())


class UserProfileHelpersTests(TestCase):
    def test_initials_uses_full_name_when_present(self):
        u = User.objects.create_user("zed", password="x")
        u.profile.full_name = "Maya Lopez"
        u.profile.save()
        self.assertEqual(u.profile.initials, "ML")
        self.assertEqual(u.profile.display_name, "Maya Lopez")

    def test_initials_falls_back_to_username_when_no_full_name(self):
        u = User.objects.create_user("xy", password="x")
        u.profile.full_name = ""
        u.profile.save()
        self.assertEqual(u.profile.initials, "XY")
        self.assertEqual(u.profile.display_name, "xy")
