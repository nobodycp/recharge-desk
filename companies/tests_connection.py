from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserProfile
from companies.models import Company
from providers.models import ProviderConnection

User = get_user_model()

HEALTH = {"redis_ok": True, "worker_ok": False, "accounts": []}


def _user(name: str, role) -> User:
    user = User.objects.create_user(name, password="x")
    UserProfile.objects.update_or_create(
        user=user, defaults={"role": role, "is_active_profile": True}
    )
    return user


class _Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = _user("mgr", UserProfile.Role.MANAGEMENT)
        cls.employee = _user("emp", UserProfile.Role.EMPLOYEE)

    def setUp(self):
        ProviderConnection.objects.all().delete()
        cache.clear()
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.manager)
        patcher = patch("providers.health.status", return_value=dict(HEALTH))
        self.health = patcher.start()
        self.addCleanup(patcher.stop)

    def company(self, name="Co", provider_key="") -> Company:
        return Company.objects.create(name=name, provider_key=provider_key)

    def url(self, name, company):
        return reverse(f"companies:{name}", kwargs={"pk": company.pk})

    def tab(self, company):
        return self.client.get(self.url("company_detail", company) + "?tab=connection")

    def post(self, name, company, data=None, **extra):
        token = str(self.tab(company).context["csrf_token"])
        return self.client.post(
            self.url(name, company),
            {**(data or {}), "csrfmiddlewaretoken": token},
            **extra,
        )


class TabAccessTests(_Base):
    def test_employee_cannot_open_connection_tab(self):
        company = self.company(provider_key="sky")
        client = Client()
        client.force_login(self.employee)
        response = client.get(self.url("company_detail", company) + "?tab=connection")
        self.assertIn(response.status_code, (302, 403))
        self.assertNotContains(response, "الربط مع المزوّد", status_code=response.status_code)

    def test_management_sees_tab_label(self):
        company = self.company()
        response = self.client.get(self.url("company_detail", company))
        self.assertContains(response, "الربط مع المزوّد")

    def test_connection_tab_is_active_and_loads_health_once(self):
        company = self.company(provider_key="aloha")
        response = self.tab(company)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.health.call_count, 1)
        self.assertContains(response, "Redis ✔ — Worker ✖")

    def test_other_tabs_do_not_load_health(self):
        company = self.company(provider_key="aloha")
        self.client.get(self.url("company_detail", company) + "?tab=general")
        self.assertEqual(self.health.call_count, 0)

    def test_htmx_partial_returns_only_connection_tab(self):
        company = self.company(provider_key="aloha")
        response = self.client.get(
            self.url("company_detail", company) + "?tab=connection&partial=connection",
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "نظام الشركة")
        self.assertNotContains(response, "<html")


class SystemTests(_Base):
    def test_post_system_sets_provider_key(self):
        company = self.company()
        response = self.post("company_connection_system", company, {"provider_key": "layan"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"], self.url("company_detail", company) + "?tab=connection"
        )
        company.refresh_from_db()
        self.assertEqual(company.provider_key, "layan")

    def test_post_system_can_clear(self):
        company = self.company(provider_key="sky")
        self.post("company_connection_system", company, {"provider_key": ""})
        company.refresh_from_db()
        self.assertEqual(company.provider_key, "")

    def test_post_invalid_system_is_rejected(self):
        company = self.company(provider_key="sky")
        response = self.post("company_connection_system", company, {"provider_key": "nope"})
        self.assertEqual(response.status_code, 302)
        company.refresh_from_db()
        self.assertEqual(company.provider_key, "sky")

    def test_system_post_requires_management(self):
        company = self.company()
        client = Client()
        client.force_login(self.employee)
        response = client.post(
            self.url("company_connection_system", company), {"provider_key": "sky"}
        )
        self.assertEqual(response.status_code, 302)
        company.refresh_from_db()
        self.assertEqual(company.provider_key, "")

    def test_empty_system_shows_select_and_no_services_form(self):
        company = self.company()
        response = self.tab(company)
        self.assertContains(response, 'name="provider_key"')
        self.assertContains(response, "نظام الشركة")
        self.assertNotContains(response, self.url("company_connection_services", company))
        self.assertNotContains(response, 'name="refresh_enabled"')
        self.assertFalse(ProviderConnection.objects.exists())


class ServicesTests(_Base):
    def test_services_form_lists_services_with_labels(self):
        company = self.company(provider_key="layan")
        response = self.tab(company)
        self.assertContains(response, self.url("company_connection_services", company))
        for label in ("تحديث الأرقام", "الشحن", "مطابقة الحسابات"):
            self.assertContains(response, label)
        self.assertContains(response, 'name="is_enabled"')
        self.assertContains(response, 'name="refresh_enabled"')
        self.assertContains(response, 'name="reconcile_enabled"')
        # Layan has no CHARGE capability: no switch, honest reason instead.
        self.assertNotContains(response, 'name="charge_enabled"')
        self.assertContains(response, "غير متاح لهذا النظام بعد")

    def test_viewing_tab_ensures_connection_row(self):
        company = self.company(provider_key="sky")
        self.tab(company)
        self.assertTrue(ProviderConnection.objects.filter(provider_key="sky").exists())

    def test_services_post_turns_refresh_off(self):
        company = self.company(provider_key="layan")
        self.post(
            "company_connection_services",
            company,
            {"is_enabled": "on", "reconcile_enabled": "on"},
        )
        connection = ProviderConnection.objects.get(provider_key="layan")
        self.assertFalse(connection.refresh_enabled)
        self.assertTrue(connection.reconcile_enabled)
        self.assertTrue(connection.is_enabled)

    def test_services_post_master_switch_off(self):
        company = self.company(provider_key="layan")
        self.post(
            "company_connection_services",
            company,
            {"refresh_enabled": "on", "reconcile_enabled": "on"},
        )
        self.assertFalse(ProviderConnection.objects.get(provider_key="layan").is_enabled)

    def test_unsupported_service_switch_is_ignored(self):
        company = self.company(provider_key="layan")
        self.post(
            "company_connection_services",
            company,
            {"is_enabled": "on", "refresh_enabled": "on", "charge_enabled": "on"},
        )
        self.assertFalse(ProviderConnection.objects.get(provider_key="layan").charge_enabled)

    def test_services_post_without_system_is_404(self):
        company = self.company()
        response = self.post("company_connection_services", company, {"is_enabled": "on"})
        self.assertEqual(response.status_code, 404)

    def test_state_badges_are_honest_for_seeded_row_without_credentials(self):
        ProviderConnection.objects.create(provider_key="sky")
        company = self.company(provider_key="sky")
        response = self.tab(company)
        self.assertContains(response, "بيانات الحساب ناقصة أو غير مقروءة")

    def test_switched_off_service_shows_off_reason(self):
        ProviderConnection.objects.create(provider_key="layan", refresh_enabled=False)
        company = self.company(provider_key="layan")
        response = self.tab(company)
        self.assertContains(response, "موقوفة")

    def test_reconcile_row_links_to_reconcile_page_when_active(self):
        company = self.company(provider_key="layan")
        response = self.tab(company)
        self.assertContains(response, self.url("layan_reconcile", company))

    def test_reconcile_link_hidden_when_service_off(self):
        ProviderConnection.objects.create(provider_key="layan", reconcile_enabled=False)
        company = self.company(provider_key="layan")
        response = self.tab(company)
        self.assertNotContains(response, self.url("layan_reconcile", company))


class AccountTests(_Base):
    def test_account_post_for_aloha_is_404(self):
        company = self.company(provider_key="aloha")
        response = self.post(
            "company_connection_account", company, {"username": "u", "password": "p"}
        )
        self.assertEqual(response.status_code, 404)

    def test_account_post_without_system_is_404(self):
        company = self.company()
        response = self.post(
            "company_connection_account", company, {"username": "u", "password": "p"}
        )
        self.assertEqual(response.status_code, 404)

    def test_account_post_for_sky_saves_username_and_never_renders_secrets(self):
        company = self.company(provider_key="sky")
        response = self.post(
            "company_connection_account",
            company,
            {
                "username": "sky_user",
                "password": "PlainPassw0rd!",
                "proxy_url": "http://px:secretproxypw@host:1",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"], self.url("company_detail", company) + "?tab=connection"
        )
        self.assertEqual(ProviderConnection.objects.get(provider_key="sky").username, "sky_user")
        body = self.tab(company).content.decode()
        self.assertIn("sky_user", body)
        self.assertNotIn("PlainPassw0rd!", body)
        self.assertNotIn("secretproxypw", body)
        self.assertIn('autocomplete="new-password"', body)

    def test_invalid_account_post_flashes_error_and_saves_nothing(self):
        company = self.company(provider_key="sky")
        response = self.post(
            "company_connection_account", company, {"username": "u", "password": ""}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ProviderConnection.objects.get(provider_key="sky").username, "")

    def test_account_post_succeeds_when_redis_session_reset_fails(self):
        company = self.company(provider_key="sky")
        with patch(
            "providers.forms.reset_session", side_effect=ConnectionError("redis down")
        ):
            response = self.post(
                "company_connection_account",
                company,
                {"username": "sky_user", "password": "PlainPassw0rd!"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"], self.url("company_detail", company) + "?tab=connection"
        )
        self.assertEqual(ProviderConnection.objects.get(provider_key="sky").username, "sky_user")

    def test_account_post_requires_management(self):
        company = self.company(provider_key="sky")
        client = Client()  # CSRF not enforced: isolate the permission check
        client.force_login(self.employee)
        response = client.post(
            self.url("company_connection_account", company),
            {"username": "u", "password": "p"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ProviderConnection.objects.filter(username="u").exists())

    def test_account_post_requires_csrf(self):
        company = self.company(provider_key="sky")
        response = self.client.post(
            self.url("company_connection_account", company),
            {"username": "u", "password": "p"},
        )
        self.assertEqual(response.status_code, 403)

    def test_account_endpoint_is_post_only(self):
        company = self.company(provider_key="sky")
        response = self.client.get(self.url("company_connection_account", company))
        self.assertEqual(response.status_code, 405)

    def test_aloha_tab_has_no_password_field(self):
        company = self.company(provider_key="aloha")
        response = self.tab(company)
        self.assertNotContains(response, 'name="password"')
        self.assertNotContains(response, "اختبار تسجيل الدخول")
        self.assertNotContains(response, self.url("company_connection_account", company))

    def test_sky_tab_has_account_card_and_test_button(self):
        company = self.company(provider_key="sky")
        response = self.tab(company)
        self.assertContains(response, 'name="password"')
        self.assertContains(response, self.url("company_connection_account", company))
        self.assertContains(response, "اختبار تسجيل الدخول")
        self.assertContains(response, 'id="connection-login-status"')


class LoginTestTests(_Base):
    def test_test_login_calls_start_and_renders_pending_partial(self):
        company = self.company(provider_key="sky")

        def fake_start(key):  # the real start() leaves this marker for the poll
            cache.set(f"providers:login-test:{key}", timezone.now().timestamp(), 150)
            return "started"

        with patch("providers.login_test.start", side_effect=fake_start) as start:
            response = self.post(
                "company_connection_test", company, HTTP_HX_REQUEST="true"
            )
        start.assert_called_once_with("sky")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'hx-trigger="every 2s"')
        self.assertContains(response, self.url("company_connection_status", company))
        self.assertTrue(ProviderConnection.objects.filter(provider_key="sky").exists())

    def test_test_login_unavailable_shows_notice_without_polling(self):
        company = self.company(provider_key="sky")
        with patch("providers.login_test.start", return_value="unavailable"):
            response = self.post(
                "company_connection_test", company, HTTP_HX_REQUEST="true"
            )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "hx-trigger")
        self.assertContains(response, "تعذّر بدء الاختبار")

    def test_test_login_non_htmx_redirects_back_to_tab(self):
        company = self.company(provider_key="sky")
        with patch("providers.login_test.start", return_value="started"):
            response = self.post("company_connection_test", company)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"], self.url("company_detail", company) + "?tab=connection"
        )

    def test_test_login_for_adapter_without_login_is_404(self):
        company = self.company(provider_key="aloha")
        with patch("providers.login_test.start") as start:
            response = self.post("company_connection_test", company, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 404)
        start.assert_not_called()

    def test_test_button_hidden_with_note_when_master_switch_off(self):
        ProviderConnection.objects.create(provider_key="sky", is_enabled=False)
        company = self.company(provider_key="sky")
        response = self.tab(company)
        self.assertNotContains(response, self.url("company_connection_test", company))
        self.assertContains(response, "فعّل الربط أولاً لاختبار تسجيل الدخول")

    def test_test_button_shown_without_note_when_master_switch_on(self):
        ProviderConnection.objects.create(provider_key="sky", is_enabled=True)
        company = self.company(provider_key="sky")
        response = self.tab(company)
        self.assertContains(response, self.url("company_connection_test", company))
        self.assertNotContains(response, "فعّل الربط أولاً")

    def test_test_login_refused_when_master_switch_off_htmx(self):
        ProviderConnection.objects.create(provider_key="sky", is_enabled=False)
        company = self.company(provider_key="sky")
        with patch("providers.login_test.start") as start:
            response = self.post("company_connection_test", company, HTTP_HX_REQUEST="true")
        start.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "فعّل الربط أولاً لاختبار تسجيل الدخول")
        self.assertNotContains(response, "hx-trigger")

    def test_test_login_refused_when_master_switch_off_non_htmx(self):
        ProviderConnection.objects.create(provider_key="sky", is_enabled=False)
        company = self.company(provider_key="sky")
        with patch("providers.login_test.start") as start:
            response = self.post("company_connection_test", company)
        start.assert_not_called()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"], self.url("company_detail", company) + "?tab=connection"
        )
        flashed = [m.message for m in get_messages(response.wsgi_request)]
        self.assertIn("فعّل الربط أولاً لاختبار تسجيل الدخول", flashed)

    def test_status_shows_no_result_hint_when_test_expired(self):
        connection = ProviderConnection.for_key("sky")
        connection.last_login_ok = True
        connection.last_login_at = timezone.now() - timezone.timedelta(hours=1)
        connection.save()
        company = self.company(provider_key="sky")
        cache.set("providers:login-test-started:sky", timezone.now().timestamp(), 3600)
        response = self.client.get(self.url("company_connection_status", company))
        self.assertContains(response, "لم تصل نتيجة من الـ worker — تأكد أنه يعمل")
        self.assertContains(response, "آخر دخول: ناجح")
        self.assertNotContains(response, "hx-trigger")

    def test_status_has_no_hint_without_expired_test(self):
        company = self.company(provider_key="sky")
        ProviderConnection.for_key("sky")
        response = self.client.get(self.url("company_connection_status", company))
        self.assertNotContains(response, "لم تصل نتيجة")

    def test_status_without_pending_test_has_no_trigger(self):
        company = self.company(provider_key="sky")
        ProviderConnection.for_key("sky")
        response = self.client.get(self.url("company_connection_status", company))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "hx-trigger")
        self.assertContains(response, 'id="connection-login-status"')

    def test_status_shows_last_result_and_escapes_error(self):
        connection = ProviderConnection.for_key("sky")
        connection.last_login_ok = False
        connection.last_login_at = timezone.now()
        connection.last_error = "<script>alert(1)</script>"
        connection.save()
        company = self.company(provider_key="sky")
        body = self.client.get(self.url("company_connection_status", company)).content.decode()
        self.assertIn("آخر دخول: فشل", body)
        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("&lt;script&gt;", body)

    def test_status_is_polling_while_pending(self):
        company = self.company(provider_key="sky")
        ProviderConnection.for_key("sky")
        cache.set("providers:login-test:sky", timezone.now().timestamp(), 150)
        response = self.client.get(self.url("company_connection_status", company))
        self.assertContains(response, 'hx-trigger="every 2s"')

    def test_status_requires_management(self):
        company = self.company(provider_key="sky")
        client = Client()
        client.force_login(self.employee)
        response = client.get(self.url("company_connection_status", company))
        self.assertEqual(response.status_code, 302)


class ReconcileGateMessageTests(_Base):
    def flashed(self, response):
        return [m.message for m in get_messages(response.wsgi_request)]

    def test_layan_with_reconcile_off_gets_the_service_reason(self):
        ProviderConnection.objects.create(provider_key="layan", reconcile_enabled=False)
        company = self.company(provider_key="layan")
        response = self.client.get(self.url("layan_reconcile", company))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.flashed(response), ["موقوفة"])

    def test_sky_not_configured_gets_the_service_reason(self):
        ProviderConnection.objects.create(provider_key="sky")
        company = self.company(provider_key="sky")
        response = self.client.get(self.url("sky_reconcile", company))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.flashed(response), ["بيانات الحساب ناقصة أو غير مقروءة"])

    def test_truly_unsupported_keeps_the_provider_only_message(self):
        layan = self.company(name="L", provider_key="layan")
        sky = self.company(name="S", provider_key="sky")
        none = self.company(name="N")
        for name, company, expected in (
            ("layan_reconcile", sky, "Layan report matching is only available for Layan."),
            ("sky_reconcile", layan, "Sky report matching is only available for Sky."),
            ("sky_reconcile", none, "Sky report matching is only available for Sky."),
        ):
            client = Client()  # fresh session: flashes would otherwise accumulate
            client.force_login(self.manager)
            response = client.get(self.url(name, company))
            self.assertEqual(response.status_code, 302)
            self.assertEqual(self.flashed(response), [expected])


class SharedSystemTests(_Base):
    def test_shared_note_lists_other_companies(self):
        company = self.company(name="Alpha", provider_key="layan")
        self.company(name="Beta Telecom", provider_key="layan")
        self.company(name="Gamma", provider_key="sky")
        response = self.tab(company)
        self.assertContains(response, "مشترك مع")
        self.assertContains(response, "Beta Telecom")
        self.assertNotContains(response, "Gamma")

    def test_no_shared_note_when_alone(self):
        company = self.company(name="Alpha", provider_key="layan")
        self.assertNotContains(self.tab(company), "مشترك مع")
