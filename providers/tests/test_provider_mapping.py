from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.test import TestCase

from companies.models import Company
from providers import service, sessions
from providers.contracts import (
    RECONCILE_NOT_SUPPORTED,
    BaseAdapter,
    Capability,
    PermanentError,
)
from providers.crypto import encrypt
from providers.models import ProviderConnection
from providers.redis_conn import get_redis


def _save_credentials(key):
    connection = ProviderConnection.for_key(key)
    connection.username = "user"
    connection.secret_encrypted = encrypt("pass")
    connection.save()
    return connection


def _company(name, provider=""):
    return Company.objects.create(
        name=name, provider_key=provider, opening_balance=0, current_balance=0
    )


class ServiceStateMappingTests(TestCase):
    def test_service_state_uses_field_not_name(self):
        by_name = _company("Sky Mobile")
        explicit = _company("Sky Mobile", "sky")
        _save_credentials("sky")
        self.assertEqual(
            service.service_state(by_name, Capability.RECONCILE).code, "unsupported"
        )
        self.assertEqual(
            service.service_state(explicit, Capability.RECONCILE).code, "active"
        )

    def test_service_state_layan_and_unknown_provider(self):
        def code(provider):
            return service.service_state(
                _company("x", provider), Capability.RECONCILE
            ).code

        self.assertEqual(code("layan"), "active")
        self.assertEqual(code("aloha"), "unsupported")
        self.assertEqual(code("nope"), "unsupported")

    def test_reconcile_with_switch_off_is_not_supported(self):
        connection = ProviderConnection.for_key("layan")
        connection.reconcile_enabled = False
        connection.save()
        with self.assertRaises(PermanentError) as ctx:
            service.reconcile(_company("L", "layan"), date(2026, 8, 1), date(2026, 8, 31))
        self.assertEqual(str(ctx.exception), RECONCILE_NOT_SUPPORTED)


class ReconcileServiceTests(TestCase):
    def setUp(self):
        get_redis().flushall()

    def test_company_without_provider_is_not_supported(self):
        with self.assertRaises(PermanentError) as ctx:
            service.reconcile(_company("Sky Mobile"), date(2026, 8, 1), date(2026, 8, 31))
        self.assertEqual(str(ctx.exception), RECONCILE_NOT_SUPPORTED)

    def test_provider_without_reconcile_capability_is_not_supported(self):
        with self.assertRaises(PermanentError) as ctx:
            service.reconcile(
                _company("Aloha Co", "aloha"), date(2026, 8, 1), date(2026, 8, 31)
            )
        self.assertEqual(str(ctx.exception), RECONCILE_NOT_SUPPORTED)

    def _run_with_clock(self, budget, now_values):
        class Fake(BaseAdapter):
            key = "sky"
            needs_login = True
            reconcile_template = "t.html"

            def reconcile(self, company, period_from, period_to, **opts):
                return ("result", opts)

        _save_credentials("sky")
        company = _company("Sky Co", "sky")
        with patch("providers.service.get_adapter", return_value=Fake()), patch(
            "providers.service.run_authenticated", side_effect=lambda a, fn, lock_wait: fn()
        ) as run, patch("providers.service.time") as clock:
            clock.monotonic.side_effect = now_values
            out, template = service.reconcile(
                company,
                date(2026, 8, 1),
                date(2026, 8, 31),
                budget=budget,
                min_amount_diff=3,
            )
        self.assertEqual(out, ("result", {"min_amount_diff": 3}))
        self.assertEqual(template, "t.html")
        return run.call_args.kwargs["lock_wait"]

    def test_lock_wait_is_bounded_by_remaining_budget(self):
        # deadline = 100 + 5; 2 s elapse before the lock wait is computed.
        self.assertEqual(self._run_with_clock(5, [100.0, 102.0]), 3.0)

    def test_lock_wait_is_capped_by_default_lock_wait(self):
        self.assertEqual(
            self._run_with_clock(45, [100.0, 100.0]), sessions.LOGIN_LOCK_WAIT_SECONDS
        )

    def test_non_login_adapter_skips_session_handling(self):
        class Fake(BaseAdapter):
            key = "layan"
            needs_login = False
            reconcile_template = "l.html"

            def reconcile(self, company, period_from, period_to, **opts):
                return "ok"

        company = _company("L", "layan")
        with patch("providers.service.get_adapter", return_value=Fake()), patch(
            "providers.service.run_authenticated"
        ) as run:
            out, template = service.reconcile(company, None, None)
        self.assertEqual((out, template), ("ok", "l.html"))
        run.assert_not_called()


class CompanyDetailButtonsTests(TestCase):
    def test_buttons_follow_provider_field_not_name(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse

        from accounts.models import UserProfile

        user = get_user_model().objects.create_user("detail_mgr", password="x")
        UserProfile.objects.update_or_create(
            user=user,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        self.client.force_login(user)
        _save_credentials("sky")
        by_name = _company("Sky Mobile")
        explicit = _company("Other", "sky")
        resp = self.client.get(reverse("companies:company_detail", args=[by_name.pk]))
        self.assertNotContains(resp, "/sky-reconcile")
        self.assertFalse(resp.context["supports_sky_reconcile"])
        resp = self.client.get(reverse("companies:company_detail", args=[explicit.pk]))
        self.assertTrue(resp.context["supports_sky_reconcile"])
        self.assertFalse(resp.context["supports_layan_reconcile"])
        self.assertContains(resp, reverse("companies:sky_reconcile", args=[explicit.pk]))
