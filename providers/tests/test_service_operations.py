"""charge / check_charge / reconcile write one provider-operations row each."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from companies.models import Company
from providers import service
from providers.contracts import (
    RECONCILE_NOT_SUPPORTED,
    AuthRequired,
    BaseAdapter,
    Capability,
    ChargeContext,
    ChargeResult,
    PermanentError,
    TransientError,
)
from providers.models import ProviderConnection, ProviderOperation
from providers.redis_conn import get_redis
from providers.registry import ADAPTERS
from providers.tests.fakes import FakeChargeAdapter

User = get_user_model()


def _ctx(sale_id: int = 41) -> ChargeContext:
    return ChargeContext(
        sale_id=sale_id,
        phone="0599000000",
        expected_cost=Decimal("10"),
        product_name="x",
        is_esim=False,
        idempotency_key="k",
    )


def _company(provider: str = "layan") -> Company:
    return Company.objects.create(
        name=f"Co {provider}", provider_key=provider, opening_balance=0, current_balance=0
    )


def _only_row() -> ProviderOperation:
    return ProviderOperation.objects.get()


class ChargeOperationTests(TestCase):
    def setUp(self):
        get_redis().flushall()
        ProviderConnection.objects.all().delete()
        self.user = User.objects.create_user("op_cashier", password="x")
        self.job_id = uuid.uuid4()

    def _charge(self, fake, **kwargs):
        kwargs.setdefault("user_id", self.user.pk)
        kwargs.setdefault("job_id", self.job_id)
        with patch.dict(ADAPTERS, {"fake": fake}):
            return service.charge("fake", _ctx(), **kwargs)

    def test_success_records_one_row_and_returns_arabic_message(self):
        fake = FakeChargeAdapter.configured(provider_ref="R-9", plan_name="Plan")
        outcome = self._charge(fake)

        self.assertEqual(outcome.result, ChargeResult.SUCCESS)
        self.assertEqual(outcome.provider_ref, "R-9")
        self.assertEqual(outcome.plan_name, "Plan")
        self.assertEqual(outcome.message, "تمّ الشحن — رقم العملية R-9")
        row = _only_row()
        self.assertEqual(
            (row.provider_key, row.service, row.outcome, row.error_code),
            ("fake", "charge", "success", ""),
        )
        self.assertEqual(row.message, "تمّ الشحن — رقم العملية R-9")
        self.assertEqual(row.phone, "0599000000")
        self.assertEqual(row.sale_id, 41)
        self.assertEqual(row.user_id, self.user.pk)
        self.assertEqual(row.job_id, self.job_id)
        self.assertEqual(row.source, "employee")
        self.assertEqual(row.attempts, 1)
        self.assertIsNotNone(row.duration_ms)
        self.assertGreaterEqual(row.duration_ms, 0)

    def test_login_failure_is_named_and_raw_code_kept_in_detail(self):
        fake = FakeChargeAdapter.configured(
            needs_login=True,
            raise_before_send=AuthRequired("expired"),
            login_error=RuntimeError("bad otp"),
        )
        with patch("providers.sessions.accounts.record_login"), self.assertLogs(
            "providers", level="WARNING"
        ):
            outcome = self._charge(fake)

        self.assertEqual(outcome.result, ChargeResult.FAILED)
        self.assertEqual(outcome.message, "فشل الشحن — خطأ تسجيل الدخول")
        row = _only_row()
        self.assertEqual(row.outcome, "failed")
        self.assertEqual(row.error_code, "login_failed")
        self.assertEqual(row.message, "فشل الشحن — خطأ تسجيل الدخول")
        self.assertEqual(row.detail, "not_sent:login_failed")

    def test_unverified_charge_is_pending(self):
        fake = FakeChargeAdapter.configured(raise_after_send=TransientError("timeout"))
        with self.assertLogs("providers.service", level="WARNING"):
            outcome = self._charge(fake)

        self.assertEqual(outcome.result, ChargeResult.UNVERIFIED)
        self.assertEqual(outcome.message, "الشحن قيد المتابعة")
        row = _only_row()
        self.assertEqual(row.outcome, "pending")
        self.assertEqual(row.error_code, "")
        self.assertEqual(row.detail, "timeout")

    def test_provider_rejection_keeps_provider_text_only_in_detail(self):
        fake = FakeChargeAdapter.configured(
            result=ChargeResult.FAILED, message="line not found token=abc123"
        )
        outcome = self._charge(fake)

        self.assertEqual(outcome.message, "فشل الشحن — خطأ في عملية الشحن")
        row = _only_row()
        self.assertEqual(row.error_code, "provider_error")
        self.assertIn("line not found", row.detail)
        self.assertNotIn("abc123", row.detail)

    def test_defaults_are_employee_source_without_user_or_job(self):
        with patch.dict(ADAPTERS, {"fake": FakeChargeAdapter.configured()}):
            service.charge("fake", _ctx())
        row = _only_row()
        self.assertEqual(row.source, "employee")
        self.assertIsNone(row.user_id)
        self.assertIsNone(row.job_id)

    def test_recording_failure_does_not_break_the_charge(self):
        fake = FakeChargeAdapter.configured(provider_ref="R-1")
        with patch("providers.service.operations.record", side_effect=RuntimeError("db")):
            outcome = self._charge(fake)
        self.assertEqual(outcome.result, ChargeResult.SUCCESS)
        self.assertEqual(outcome.provider_ref, "R-1")
        self.assertEqual(outcome.message, "تمّ الشحن — رقم العملية R-1")

    def test_unknown_provider_still_raises_keyerror_and_records_nothing(self):
        with self.assertRaises(KeyError):
            service.charge("nope", _ctx())
        self.assertFalse(ProviderOperation.objects.exists())


class CheckChargeOperationTests(TestCase):
    def setUp(self):
        get_redis().flushall()
        ProviderConnection.objects.all().delete()

    def test_failed_check_records_check_charge_row_from_system(self):
        fake = FakeChargeAdapter.configured(
            check_result=ChargeResult.FAILED, message="no charge"
        )
        job_id = uuid.uuid4()
        with patch.dict(ADAPTERS, {"fake": fake}):
            outcome = service.check_charge("fake", _ctx(), "ref-1", job_id=job_id)

        self.assertEqual(outcome.result, ChargeResult.FAILED)
        self.assertEqual(outcome.message, "فشل متابعة الشحن — خطأ في عملية متابعة الشحن")
        row = _only_row()
        self.assertEqual(row.service, "check_charge")
        self.assertEqual(row.source, "system")
        self.assertEqual(row.outcome, "failed")
        self.assertEqual(row.sale_id, 41)
        self.assertEqual(row.job_id, job_id)
        self.assertEqual(row.phone, "0599000000")
        self.assertEqual(row.detail, "no charge")

    def test_success_check_is_recorded_with_ref(self):
        fake = FakeChargeAdapter.configured(provider_ref="R-5")
        with patch.dict(ADAPTERS, {"fake": fake}):
            outcome = service.check_charge("fake", _ctx(), "R-5")
        self.assertEqual(outcome.message, "تمّت متابعة الشحن — رقم العملية R-5")
        self.assertEqual(_only_row().outcome, "success")

    def test_check_error_is_pending_and_never_raises(self):
        fake = FakeChargeAdapter.configured(check_error=TransientError("timeout"))
        with patch.dict(ADAPTERS, {"fake": fake}), self.assertLogs(
            "providers.service", level="WARNING"
        ):
            outcome = service.check_charge("fake", _ctx(), "ref-1")
        self.assertEqual(outcome.result, ChargeResult.UNVERIFIED)
        self.assertEqual(outcome.message, "الشحن قيد المتابعة")
        self.assertEqual(_only_row().outcome, "pending")

    def test_recording_failure_does_not_break_check_charge(self):
        fake = FakeChargeAdapter.configured()
        with patch.dict(ADAPTERS, {"fake": fake}), patch(
            "providers.service.operations.record", side_effect=RuntimeError("db")
        ):
            outcome = service.check_charge("fake", _ctx(), "ref-1")
        self.assertEqual(outcome.result, ChargeResult.SUCCESS)

    def test_missing_ref_early_exit_calls_no_provider_and_records_nothing(self):
        fake = FakeChargeAdapter.configured(check_without_ref=False)
        with patch.dict(ADAPTERS, {"fake": fake}):
            outcome = service.check_charge("fake", _ctx(), "")
        self.assertEqual(outcome.result, ChargeResult.UNVERIFIED)
        self.assertFalse(ProviderOperation.objects.exists())


class _Reconciler(BaseAdapter):
    key = "layan"
    needs_login = False
    reconcile_template = "t.html"
    result = SimpleNamespace(row_count=7)
    error: Exception | None = None

    def reconcile(self, company, period_from, period_to, **opts):
        if self.error is not None:
            raise self.error
        return self.result


class ReconcileOperationTests(TestCase):
    def setUp(self):
        get_redis().flushall()
        ProviderConnection.objects.all().delete()
        self.user = User.objects.create_user("op_manager", password="x")

    def _run(self, company, adapter, **kwargs):
        with patch("providers.service.get_adapter", return_value=adapter):
            return service.reconcile(
                company, date(2026, 8, 1), date(2026, 8, 31), **kwargs
            )

    def test_success_records_row_with_row_count(self):
        result, template = self._run(_company(), _Reconciler(), user_id=self.user.pk)

        self.assertEqual(template, "t.html")
        self.assertEqual(result.row_count, 7)
        row = _only_row()
        self.assertEqual(
            (row.provider_key, row.service, row.outcome, row.error_code),
            ("layan", "reconcile", "success", ""),
        )
        self.assertEqual(row.message, "تمّت المطابقة")
        self.assertEqual(row.source, "management")
        self.assertEqual(row.user_id, self.user.pk)
        self.assertEqual(row.phone, "")
        self.assertEqual(row.detail, "7 rows")
        self.assertIsNotNone(row.duration_ms)

    def test_result_without_row_count_has_empty_detail(self):
        adapter = _Reconciler()
        adapter.result = "plain"
        self._run(_company(), adapter)
        self.assertEqual(_only_row().detail, "")

    def test_exception_is_recorded_and_reraised_unchanged(self):
        adapter = _Reconciler()
        adapter.error = TransientError("connection_error")
        with self.assertRaises(TransientError) as ctx:
            self._run(_company(), adapter, user_id=self.user.pk)

        self.assertIs(ctx.exception, adapter.error)
        row = _only_row()
        self.assertEqual(row.outcome, "failed")
        self.assertEqual(row.error_code, "connection")
        self.assertEqual(row.message, "فشل المطابقة — تعذّر الاتصال بالمزوّد")
        self.assertEqual(row.user_id, self.user.pk)

    def test_unexpected_exception_is_internal_and_reraised(self):
        adapter = _Reconciler()
        adapter.error = ValueError("bad workbook")
        with self.assertRaises(ValueError):
            self._run(_company(), adapter)
        row = _only_row()
        self.assertEqual(row.error_code, "internal")
        self.assertIn("bad workbook", row.detail)

    def test_gate_switched_off_records_service_off_and_raises(self):
        connection = ProviderConnection.for_key("layan")
        connection.reconcile_enabled = False
        connection.save()
        with self.assertRaises(PermanentError) as ctx:
            self._run(_company(), _Reconciler(), user_id=self.user.pk)

        self.assertEqual(str(ctx.exception), RECONCILE_NOT_SUPPORTED)
        row = _only_row()
        self.assertEqual(row.provider_key, "layan")
        self.assertEqual(row.service, "reconcile")
        self.assertEqual(row.outcome, "failed")
        self.assertEqual(row.error_code, "service_off")
        self.assertEqual(row.message, "فشل المطابقة — الخدمة موقوفة")
        self.assertEqual(row.source, "management")
        self.assertEqual(row.user_id, self.user.pk)

    def test_gate_not_configured_records_not_configured(self):
        with patch(
            "providers.accounts.get_credentials", side_effect=PermanentError("config_error")
        ):
            with self.assertRaises(PermanentError) as ctx:
                service.reconcile(_company("sky"), date(2026, 8, 1), date(2026, 8, 31))
        self.assertEqual(str(ctx.exception), RECONCILE_NOT_SUPPORTED)
        row = _only_row()
        self.assertEqual(row.provider_key, "sky")
        self.assertEqual(row.error_code, "not_configured")
        self.assertEqual(row.message, "فشل المطابقة — بيانات الحساب ناقصة")

    def test_unsupported_and_missing_system_write_nothing(self):
        for company in (_company(""), _company("aloha"), _company("nope")):
            with self.assertRaises(PermanentError) as ctx:
                service.reconcile(company, date(2026, 8, 1), date(2026, 8, 31))
            self.assertEqual(str(ctx.exception), RECONCILE_NOT_SUPPORTED)
        self.assertFalse(ProviderOperation.objects.exists())

    def test_recording_failure_does_not_change_reconcile(self):
        with patch("providers.service.operations.record", side_effect=RuntimeError("db")):
            result, _template = self._run(_company(), _Reconciler())
        self.assertEqual(result.row_count, 7)
