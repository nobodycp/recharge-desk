from __future__ import annotations

import importlib
from datetime import date
from unittest.mock import patch

from django.apps import apps
from django.test import TestCase

from companies.models import Company
from providers import service, sessions
from providers.contracts import (
    RECONCILE_NOT_SUPPORTED,
    BaseAdapter,
    Capability,
    PermanentError,
)
from providers.redis_conn import get_redis

migration = importlib.import_module("companies.migrations.0012_fill_provider_from_name")


def _company(name, provider=""):
    return Company.objects.create(
        name=name, phone_refresh_provider=provider, opening_balance=0, current_balance=0
    )


class CompanySupportsTests(TestCase):
    def test_company_supports_uses_field_not_name(self):
        by_name = _company("Sky Mobile")
        explicit = _company("Sky Mobile", "sky")
        self.assertFalse(service.company_supports(by_name, Capability.RECONCILE))
        self.assertTrue(service.company_supports(explicit, Capability.RECONCILE))

    def test_company_supports_layan_and_unknown_provider(self):
        self.assertTrue(
            service.company_supports(_company("x", "layan"), Capability.RECONCILE)
        )
        self.assertFalse(
            service.company_supports(_company("x", "aloha"), Capability.RECONCILE)
        )
        self.assertFalse(
            service.company_supports(_company("x", "nope"), Capability.RECONCILE)
        )


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
        by_name = _company("Sky Mobile")
        explicit = _company("Other", "sky")
        resp = self.client.get(reverse("companies:company_detail", args=[by_name.pk]))
        self.assertNotContains(resp, "/sky-reconcile")
        self.assertFalse(resp.context["supports_sky_reconcile"])
        resp = self.client.get(reverse("companies:company_detail", args=[explicit.pk]))
        self.assertTrue(resp.context["supports_sky_reconcile"])
        self.assertFalse(resp.context["supports_layan_reconcile"])
        self.assertContains(resp, reverse("companies:sky_reconcile", args=[explicit.pk]))


class MappingReportCommandTests(TestCase):
    def test_reports_empty_and_mismatched_without_changing_anything(self):
        from io import StringIO

        from django.core.management import call_command

        _company("Sky Mobile")  # empty, name suggests sky
        _company("Plain Co")  # empty, no suggestion
        _company("Layan Direct", "layan")  # consistent
        _company("Aloha Shop", "sky")  # field disagrees with the name
        out = StringIO()
        call_command("providers_mapping_report", stdout=out)
        text = out.getvalue()
        self.assertIn("empty provider (2)", text)
        self.assertIn("Sky Mobile (name suggests: sky)", text)
        self.assertIn("Plain Co", text)
        self.assertIn("another provider (1)", text)
        self.assertIn("Aloha Shop: field=sky, name suggests aloha", text)
        self.assertNotIn("Layan Direct", text)
        self.assertEqual(
            Company.objects.get(name="Sky Mobile").phone_refresh_provider, ""
        )


class FillProviderMigrationTests(TestCase):
    def test_fill_provider_migration_maps_names(self):
        cases = {
            "Sky Mobile": "sky",
            "SKY": "sky",
            "  sky 5g  ": "sky",
            "سكاي موبايل": "sky",
            "Aloha": "aloha",
            "ALOHA Telecom": "aloha",
            "Layan Test": "layan",
            "LAYAN": "layan",
            "Areen": "areen",
            "Sky and Layan": "sky",  # same priority as COMPANY_TO_PROVIDER
            "Layan سكاي": "layan",  # refresh rule beat the Arabic reconcile rule
            "Other Co": "",
        }
        companies = {name: _company(name) for name in cases}
        keep = _company("Sky Keeps Explicit", "areen")
        migration.fill_provider_from_name(apps, None)
        for name, expected in cases.items():
            companies[name].refresh_from_db()
            self.assertEqual(
                companies[name].phone_refresh_provider, expected, f"name={name!r}"
            )
        keep.refresh_from_db()
        self.assertEqual(keep.phone_refresh_provider, "areen")
