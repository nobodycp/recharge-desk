from __future__ import annotations

import os
from unittest import mock

from django.test import TestCase

from companies.models import Company
from providers.contracts import Capability
from providers.crypto import encrypt
from providers.models import ProviderConnection
from providers.service import ServiceState, service_enabled, service_state

SKY_ENV_KEYS = (
    "SKY_SALES_USER",
    "SKY_SALES_PASSWORD",
    "SKY_SALES_TOTP_SECRET",
    "SKY_SALES_OTP_AUTH_URI",
    "SKY_SALES_PROXY",
)


def _company(provider=""):
    return Company(name="Co", provider_key=provider)


def _save_credentials(key):
    conn = ProviderConnection.for_key(key)
    conn.username = "user"
    conn.secret_encrypted = encrypt("pass")
    conn.save()
    return conn


class _CleanEnvMixin:
    def setUp(self):
        super().setUp()
        env = {k: v for k, v in os.environ.items() if k not in SKY_ENV_KEYS}
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)


class ServiceStateTests(_CleanEnvMixin, TestCase):
    def test_no_system_is_unsupported(self):
        state = service_state(_company(""), Capability.REFRESH)
        self.assertEqual(state, ServiceState("unsupported", "لم يتم تحديد نظام الشركة"))

    def test_unknown_key_is_unsupported(self):
        state = service_state(_company("nope"), Capability.REFRESH)
        self.assertEqual(state.code, "unsupported")
        self.assertEqual(state.reason, "لم يتم تحديد نظام الشركة")

    def test_provider_mismatch_is_unsupported(self):
        state = service_state(_company("sky"), Capability.RECONCILE, provider="layan")
        self.assertEqual(state.code, "unsupported")
        self.assertEqual(state.reason, "غير متاح لهذا النظام بعد")

    def test_capability_missing_from_adapter_is_unsupported(self):
        state = service_state(_company("aloha"), Capability.CHARGE)
        self.assertEqual(state, ServiceState("unsupported", "غير متاح لهذا النظام بعد"))

    def test_login_adapter_without_credentials_is_not_configured(self):
        # A seeded connection row (no credentials) is not an account.
        ProviderConnection.for_key("sky")
        state = service_state(_company("sky"), Capability.RECONCILE)
        self.assertEqual(
            state, ServiceState("not_configured", "بيانات الحساب ناقصة أو غير مقروءة")
        )

    def test_undecryptable_credentials_are_not_configured_without_raising(self):
        conn = ProviderConnection.for_key("sky")
        conn.username = "user"
        conn.secret_encrypted = "garbage"
        conn.save()
        state = service_state(_company("sky"), Capability.RECONCILE)
        self.assertEqual(state.code, "not_configured")

    def test_env_credentials_make_sky_configured(self):
        with mock.patch.dict(
            os.environ, {"SKY_SALES_USER": "u", "SKY_SALES_PASSWORD": "p"}
        ):
            state = service_state(_company("sky"), Capability.RECONCILE)
        self.assertEqual(state.code, "active")

    def test_master_switch_off_is_off_not_not_configured(self):
        conn = _save_credentials("sky")
        conn.is_enabled = False
        conn.save()
        state = service_state(_company("sky"), Capability.RECONCILE)
        self.assertEqual(state, ServiceState("off", "موقوفة"))

    def test_master_switch_off_without_credentials_is_off(self):
        conn = ProviderConnection.for_key("sky")
        conn.is_enabled = False
        conn.save()
        self.assertEqual(
            service_state(_company("sky"), Capability.RECONCILE).code, "off"
        )

    def test_service_switch_off_is_off(self):
        conn = _save_credentials("sky")
        conn.reconcile_enabled = False
        conn.save()
        state = service_state(_company("sky"), Capability.RECONCILE)
        self.assertEqual(state, ServiceState("off", "موقوفة"))

    def test_active_sky_reconcile_with_saved_credentials(self):
        _save_credentials("sky")
        state = service_state(_company("sky"), Capability.RECONCILE)
        self.assertEqual(state, ServiceState("active", ""))

    def test_no_login_adapter_active_without_credentials(self):
        state = service_state(_company("aloha"), Capability.REFRESH)
        self.assertEqual(state, ServiceState("active", ""))

    def test_provider_argument_matching_is_active(self):
        _save_credentials("layan")
        state = service_state(_company("layan"), Capability.RECONCILE, provider="layan")
        self.assertEqual(state.code, "active")

    def test_layan_refresh_stays_active_without_portal_credentials(self):
        state = service_state(_company("layan"), Capability.REFRESH)
        self.assertEqual(state, ServiceState("active", ""))

    def test_layan_number_check_needs_portal_credentials(self):
        state = service_state(_company("layan"), Capability.NUMBER_INFO)
        self.assertEqual(state.code, "not_configured")

    def test_layan_charge_needs_credentials_and_switch(self):
        conn = ProviderConnection.for_key("layan")
        conn.charge_enabled = True
        conn.save()
        self.assertEqual(
            service_state(_company("layan"), Capability.CHARGE).code, "not_configured"
        )
        _save_credentials("layan")
        self.assertEqual(
            service_state(_company("layan"), Capability.CHARGE).code, "active"
        )
        conn = ProviderConnection.for_key("layan")
        conn.charge_enabled = False
        conn.save()
        self.assertEqual(
            service_state(_company("layan"), Capability.CHARGE).code, "off"
        )

    def test_service_state_does_not_create_rows(self):
        before = ProviderConnection.objects.count()
        service_state(_company("aloha"), Capability.REFRESH)
        service_state(_company("sky"), Capability.RECONCILE)
        self.assertEqual(ProviderConnection.objects.count(), before)


class ServiceEnabledTests(TestCase):
    def setUp(self):
        ProviderConnection.objects.all().delete()

    def test_no_row_uses_field_defaults_and_creates_nothing(self):
        self.assertTrue(service_enabled("aloha", Capability.REFRESH))
        self.assertFalse(service_enabled("aloha", Capability.CHARGE))
        self.assertTrue(service_enabled("aloha", Capability.RECONCILE))
        self.assertEqual(ProviderConnection.objects.count(), 0)

    def test_service_switch_and_master_switch(self):
        conn = ProviderConnection.for_key("layan")
        conn.refresh_enabled = False
        conn.save()
        self.assertFalse(service_enabled("layan", Capability.REFRESH))
        self.assertTrue(service_enabled("layan", Capability.RECONCILE))
        conn.is_enabled = False
        conn.save()
        self.assertFalse(service_enabled("layan", Capability.RECONCILE))

    def test_check_charge_follows_charge_switch(self):
        conn = ProviderConnection.for_key("layan")
        self.assertFalse(service_enabled("layan", Capability.CHECK_CHARGE))
        conn.charge_enabled = True
        conn.save()
        self.assertTrue(service_enabled("layan", Capability.CHECK_CHARGE))

    def test_unmapped_capability_is_not_gated_by_a_switch(self):
        ProviderConnection.for_key("layan")
        self.assertTrue(service_enabled("layan", Capability.REFRESH))


class RefreshGateTests(TestCase):
    PHONE = "0529000000"

    def setUp(self):
        from phone_refresh.models import SystemSettings
        from providers.redis_conn import get_redis

        get_redis().flushall()
        settings_row = SystemSettings.get()
        settings_row.db_precheck_enabled = True
        settings_row.save()
        patcher = mock.patch(
            "phone_refresh.services.refresh_service._lookup_provider_for_phone",
            return_value="sky",
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_refresh_switch_off_returns_provider_off(self):
        from phone_refresh.services.refresh_service import refresh_phone

        conn = ProviderConnection.for_key("sky")
        conn.refresh_enabled = False
        conn.save()
        with mock.patch(
            "phone_refresh.services.refresh_service.provider_service.refresh"
        ) as refresh:
            result = refresh_phone(self.PHONE)
        self.assertEqual(result.status.code, "provider_off")
        refresh.assert_not_called()

    def test_admin_bypass_ignores_refresh_switch(self):
        from phone_refresh.services.refresh_service import refresh_phone
        from providers.contracts import RawResponse

        conn = ProviderConnection.for_key("sky")
        conn.refresh_enabled = False
        conn.save()
        with mock.patch(
            "phone_refresh.services.refresh_service.provider_service.refresh",
            return_value=RawResponse(text="", json=None, status_code=0, error="x"),
        ) as refresh:
            result = refresh_phone(
                self.PHONE, internal_test=True, bypass_provider_off=True
            )
        refresh.assert_called_once_with("sky", self.PHONE)
        self.assertNotEqual(result.status.code, "provider_off")
