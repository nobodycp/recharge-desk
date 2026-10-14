"""Refresh operations are recorded once each in the provider operations log."""
from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase

from phone_refresh.models import RefreshLog, RefreshSource, SystemSettings
from phone_refresh.services import refresh_jobs
from phone_refresh.services.refresh_service import (
    complete_failure,
    refresh_phone,
    result_payload,
)
from providers import service as provider_service
from providers.adapters.sky.adapter import SkyAdapter
from providers.contracts import AuthRequired, RawResponse, TransientError
from providers.models import ProviderJob, ProviderOperation
from providers.redis_conn import get_redis

PHONE = "0529000000"
PROVIDER_REFRESH = "providers.service.refresh"
REFRESHED = {"reason": "success"}


def _raw(data=REFRESHED, **kwargs) -> RawResponse:
    return RawResponse(text="", json=data, status_code=200, **kwargs)


class _OperationsTestCase(TestCase):
    def setUp(self):
        s = SystemSettings.get()
        s.db_precheck_enabled = False
        s.default_provider = "sky"
        s.show_last_refresh = False
        s.save()
        get_redis().flushall()

    def refresh(self, phone=PHONE, **kwargs):
        kwargs.setdefault("source", RefreshSource.WEB)
        return refresh_phone(phone, ip="1.2.3.4", **kwargs)

    def only_row(self) -> ProviderOperation:
        self.assertEqual(ProviderOperation.objects.count(), 1)
        return ProviderOperation.objects.get()


class RefreshOutcomeRowTests(_OperationsTestCase):
    @patch(PROVIDER_REFRESH, return_value=_raw())
    def test_refreshed_reply_writes_one_success_row(self, refresh):
        result = self.refresh()
        row = self.only_row()
        self.assertEqual(result.status.code, "refreshed")
        self.assertEqual(row.service, "refresh")
        self.assertEqual(row.provider_key, "sky")
        self.assertEqual(row.phone, PHONE)
        self.assertEqual(row.source, "web")
        self.assertEqual(row.outcome, "success")
        self.assertEqual(row.error_code, "")
        self.assertEqual(row.message, "تمّ التحديث")
        self.assertIsNone(row.job_id)
        self.assertEqual(row.attempts, 1)
        log = RefreshLog.objects.get()
        self.assertEqual(row.duration_ms, log.duration_ms)

    @patch(PROVIDER_REFRESH, return_value=_raw({"reason": "success", "task_id": "T-77"}))
    def test_success_row_carries_the_task_id(self, refresh):
        self.refresh()
        self.assertEqual(self.only_row().message, "تمّ التحديث — رقم العملية T-77")

    @patch(PROVIDER_REFRESH, return_value=RawResponse(text="ok", json=["x"], status_code=200))
    def test_non_dict_json_gives_no_ref(self, refresh):
        # The list reply matches nothing -> error status; the row still has no ref.
        self.refresh()
        self.assertNotIn("رقم العملية", self.only_row().message)

    @patch(
        PROVIDER_REFRESH,
        return_value=RawResponse(text="", json=None, status_code=0, error="login_failed"),
    )
    def test_login_failed_row_and_unchanged_customer_result(self, refresh):
        result = self.refresh()
        row = self.only_row()
        self.assertEqual(row.outcome, "failed")
        self.assertEqual(row.error_code, "login_failed")
        self.assertEqual(row.message, "فشل التحديث — خطأ تسجيل الدخول")
        self.assertEqual(row.detail, "error: login_failed")
        # Customer-facing result and RefreshLog are exactly as before.
        self.assertEqual(result.status.code, "busy")
        self.assertEqual(result.error, "login_failed")
        self.assertEqual(result_payload(result)["status"], "busy")
        log = RefreshLog.objects.get()
        self.assertEqual(log.status.code, "busy")
        self.assertEqual(log.raw_excerpt, "error: login_failed")

    @patch(PROVIDER_REFRESH, return_value=_raw({"reason": "not_found"}))
    def test_not_found_reply_is_a_rejection_with_the_status_label(self, refresh):
        result = self.refresh()
        row = self.only_row()
        self.assertEqual(result.status.code, "not_found")
        self.assertEqual(row.outcome, "failed")
        self.assertEqual(row.error_code, "rejected")
        self.assertIn(result.status.label, row.message)
        self.assertEqual(row.message, f"فشل التحديث — رفض المزوّد: {result.status.label}")

    @patch(PROVIDER_REFRESH, side_effect=RuntimeError("boom secret"))
    def test_provider_crash_is_an_internal_error_row(self, refresh):
        with self.assertLogs("phone_refresh.services.refresh_service", level="ERROR"):
            result = self.refresh()
        row = self.only_row()
        self.assertEqual(result.status.code, "error")
        self.assertEqual(row.outcome, "failed")
        self.assertEqual(row.error_code, "internal")
        self.assertEqual(row.message, "فشل التحديث — خطأ داخلي")
        self.assertEqual(row.attempts, 1)
        self.assertEqual(RefreshLog.objects.get().status.code, "error")

    def test_enqueue_failure_row_is_a_busy_login_busy_failure(self):
        result = complete_failure(
            phone=PHONE,
            provider_key="sky",
            error="enqueue_failed",
            ip=None,
            source=RefreshSource.WEB,
            started=0.0,
            status_code="busy",
        )
        row = self.only_row()
        self.assertEqual(result.status.code, "busy")
        self.assertEqual(row.error_code, "login_busy")
        self.assertEqual(
            row.message, "فشل التحديث — تسجيل الدخول قيد التنفيذ، حاول بعد قليل"
        )
        self.assertEqual(row.detail, "enqueue_failed")


class PreCheckWritesNothingTests(_OperationsTestCase):
    @patch(PROVIDER_REFRESH, return_value=_raw())
    def test_invalid_number_writes_no_row(self, refresh):
        result = self.refresh("123")
        self.assertEqual(result.status.code, "not_found")
        refresh.assert_not_called()
        self.assertEqual(ProviderOperation.objects.count(), 0)

    @patch(PROVIDER_REFRESH, return_value=_raw())
    def test_rate_limited_refresh_writes_no_second_row(self, refresh):
        self.refresh()
        self.assertEqual(ProviderOperation.objects.count(), 1)
        second = self.refresh()
        self.assertEqual(second.status.code, "wait")
        self.assertEqual(refresh.call_count, 1)
        self.assertEqual(ProviderOperation.objects.count(), 1)

    @patch(PROVIDER_REFRESH, return_value=_raw())
    def test_service_off_writes_no_row(self, refresh):
        SystemSettings.objects.filter(pk=1).update(service_enabled=False)
        result = self.refresh()
        self.assertEqual(result.status.code, "service_off")
        self.assertEqual(ProviderOperation.objects.count(), 0)


class RefreshSourceMappingTests(_OperationsTestCase):
    @patch(PROVIDER_REFRESH, return_value=_raw())
    def test_internal_test_source_is_management(self, refresh):
        self.refresh(source=RefreshSource.INTERNAL_TEST, internal_test=True)
        self.assertEqual(self.only_row().source, "management")

    @patch(PROVIDER_REFRESH, return_value=_raw())
    def test_known_sources_map_one_to_one(self, refresh):
        expected = {
            RefreshSource.WEB: "web",
            RefreshSource.SMS: "sms",
            RefreshSource.API: "api",
            RefreshSource.EMPLOYEE: "employee",
            RefreshSource.INTERNAL_TEST: "management",
        }
        for source, operation_source in expected.items():
            ProviderOperation.objects.all().delete()
            RefreshLog.objects.all().delete()  # no cooldown between iterations
            with self.subTest(source=source), patch(
                "providers.operations.log.warning"
            ) as warning:
                self.refresh(source=source, internal_test=(source == RefreshSource.INTERNAL_TEST))
                self.assertEqual(self.only_row().source, operation_source)
                warning.assert_not_called()  # mapped explicitly, not by record()'s fallback


class RefreshJobRowTests(_OperationsTestCase):
    @patch(PROVIDER_REFRESH, return_value=_raw())
    def test_async_job_row_has_job_id_and_job_source(self, refresh):
        with self.captureOnCommitCallbacks(execute=True):
            ticket = refresh_jobs.submit_refresh(
                PHONE, ip="1.2.3.4", source=RefreshSource.SMS
            )
        job = ProviderJob.objects.get(pk=ticket.job_id)
        row = self.only_row()
        self.assertEqual(str(row.job_id), str(job.pk))
        self.assertEqual(row.source, "sms")
        self.assertEqual(row.outcome, "success")

    @patch(PROVIDER_REFRESH, side_effect=RuntimeError("worker crash"))
    def test_async_provider_crash_row_has_job_id(self, refresh):
        with self.assertLogs("phone_refresh.tasks", level="ERROR"):
            with self.captureOnCommitCallbacks(execute=True):
                ticket = refresh_jobs.submit_refresh(
                    PHONE, ip=None, source=RefreshSource.EMPLOYEE
                )
        row = self.only_row()
        self.assertEqual(str(row.job_id), str(ProviderJob.objects.get(pk=ticket.job_id).pk))
        self.assertEqual(row.source, "employee")
        self.assertEqual(row.error_code, "internal")


class RefreshAttemptsTests(_OperationsTestCase):
    @patch("providers.service.time.sleep")
    @patch.object(
        SkyAdapter,
        "refresh",
        side_effect=[TransientError("timeout"), _raw()],
    )
    def test_row_records_the_real_attempt_count(self, adapter_refresh, sleep):
        result = self.refresh()
        row = self.only_row()
        self.assertEqual(result.status.code, "refreshed")
        self.assertEqual(adapter_refresh.call_count, 2)
        self.assertEqual(row.attempts, 2)
        self.assertEqual(row.outcome, "success")

    @patch("providers.service.time.sleep")
    @patch.object(SkyAdapter, "refresh", side_effect=TransientError("timeout"))
    def test_exhausted_retries_record_all_attempts(self, adapter_refresh, sleep):
        result = self.refresh()
        row = self.only_row()
        self.assertEqual(result.status.code, "error")
        self.assertEqual(row.attempts, provider_service.REFRESH_ATTEMPTS)
        self.assertEqual(row.error_code, "connection")

    def test_service_refresh_sets_attempts_on_every_response(self):
        with patch.object(SkyAdapter, "refresh", return_value=_raw()):
            self.assertEqual(provider_service.refresh("sky", PHONE).attempts, 1)
        with patch.object(SkyAdapter, "refresh", side_effect=AuthRequired("x")), patch.object(
            SkyAdapter, "login", side_effect=RuntimeError("nope")
        ):
            raw = provider_service.refresh("sky", PHONE)
        self.assertEqual(raw.error, "login_failed")
        self.assertEqual(raw.attempts, 1)
        with patch("providers.service.time.sleep"), patch.object(
            SkyAdapter, "refresh", side_effect=TransientError("timeout")
        ):
            raw = provider_service.refresh("sky", PHONE)
        self.assertEqual(raw.attempts, provider_service.REFRESH_ATTEMPTS)
        # Budget spent before any attempt starts: nothing was attempted.
        raw = provider_service.refresh("sky", PHONE, budget=0)
        self.assertEqual(raw.error, "sync_budget_exceeded")
        self.assertEqual(raw.attempts, 0)


class RefreshAutoLoginTests(_OperationsTestCase):
    def test_login_then_success_is_exactly_one_success_row(self):
        with patch.object(
            SkyAdapter, "refresh", side_effect=[AuthRequired("expired"), _raw()]
        ) as adapter_refresh, patch.object(SkyAdapter, "login") as login:
            result = self.refresh()
        self.assertEqual(result.status.code, "refreshed")
        login.assert_called_once()
        self.assertEqual(adapter_refresh.call_count, 2)
        row = self.only_row()
        self.assertEqual(row.outcome, "success")
        self.assertEqual(row.attempts, 1)
        self.assertEqual(RefreshLog.objects.count(), 1)
