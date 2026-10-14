"""``service.number_info``, ``run_number_info_job`` and number-info job retention."""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from providers import jobs, service
from providers.contracts import (
    AuthRequired,
    BaseAdapter,
    Capability,
    NumberInfo,
    PermanentError,
    TransientError,
)
from providers.models import ProviderConnection, ProviderJob, ProviderOperation
from providers.redis_conn import get_redis
from providers.registry import ADAPTERS
from providers.tasks import NUMBER_INFO_BUDGET_SECONDS, purge_old_jobs, run_number_info_job

User = get_user_model()

PHONE = "0525737739"
INFO = NumberInfo(
    network="Sky",
    is_esim=True,
    status="active",
    plan_name="Plan A",
    plan_price=Decimal("25.50"),
    data_used_gb=Decimal("1.5"),
    minutes_used=10,
)


class FakeInfoAdapter(BaseAdapter):
    """Number-info adapter; ``script`` entries are returned or raised in order."""

    key = "fakeinfo"
    capabilities = frozenset({Capability.NUMBER_INFO})
    needs_login = False
    script: list = []
    calls: list = []
    logins = 0

    @classmethod
    def scripted(cls, *script, needs_login=False):
        return type(
            "FakeInfoAdapter",
            (cls,),
            {"script": list(script), "calls": [], "logins": 0, "needs_login": needs_login},
        )

    def number_info(self, phone):
        type(self).calls.append(phone)
        step = type(self).script.pop(0) if type(self).script else INFO
        if isinstance(step, Exception):
            raise step
        return step

    def login(self):
        type(self).logins += 1


def _only_row() -> ProviderOperation:
    return ProviderOperation.objects.get()


class _Base(TestCase):
    def setUp(self):
        get_redis().flushall()
        ProviderConnection.objects.all().delete()

    def _run(self, adapter_cls, *args, **kwargs):
        with patch.dict(ADAPTERS, {"fakeinfo": adapter_cls}):
            return service.number_info("fakeinfo", PHONE, *args, **kwargs)


class NumberInfoServiceTests(_Base):
    def test_success_returns_info_and_records_one_row(self):
        user = User.objects.create_user("ni_user", password="x")
        job_id = uuid.uuid4()
        adapter = FakeInfoAdapter.scripted(INFO)

        result = self._run(adapter, source="management", user_id=user.pk, sale_id=77, job_id=job_id)

        self.assertTrue(result.ok)
        self.assertEqual(result.info, INFO)
        self.assertEqual(result.classified.outcome, "success")
        self.assertEqual(result.classified.message, "تمّ فحص الرقم")
        self.assertEqual(adapter.calls, [PHONE])
        row = _only_row()
        self.assertEqual(
            (row.provider_key, row.service, row.outcome, row.error_code, row.message),
            ("fakeinfo", "number_info", "success", "", "تمّ فحص الرقم"),
        )
        self.assertEqual(row.phone, PHONE)
        self.assertEqual(row.source, "management")
        self.assertEqual(row.user_id, user.pk)
        self.assertEqual(row.sale_id, 77)
        self.assertEqual(row.job_id, job_id)
        self.assertEqual(row.attempts, 1)
        self.assertIsNotNone(row.duration_ms)

    def test_defaults_to_employee_source(self):
        self._run(FakeInfoAdapter.scripted(INFO))
        row = _only_row()
        self.assertEqual(row.source, "employee")
        self.assertIsNone(row.user_id)
        self.assertIsNone(row.sale_id)
        self.assertIsNone(row.job_id)

    def test_auth_required_then_login_then_success_is_one_row_one_attempt(self):
        adapter = FakeInfoAdapter.scripted(AuthRequired("expired"), INFO, needs_login=True)
        with patch("providers.sessions.accounts.record_login"):
            result = self._run(adapter)

        self.assertTrue(result.ok)
        self.assertEqual(adapter.logins, 1)
        self.assertEqual(len(adapter.calls), 2)
        row = _only_row()
        self.assertEqual(row.outcome, "success")
        self.assertEqual(row.attempts, 1)

    def test_transient_errors_are_retried_three_times_with_one_failed_row(self):
        adapter = FakeInfoAdapter.scripted(*[TransientError("connection_error")] * 3)
        with patch("providers.service.time.sleep") as sleep, self.assertLogs(
            "providers.service", level="WARNING"
        ):
            result = self._run(adapter)

        self.assertFalse(result.ok)
        self.assertIsNone(result.info)
        self.assertEqual(len(adapter.calls), 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [1, 2])
        self.assertEqual(result.classified.error_code, "connection")
        self.assertEqual(result.classified.message, "فشل فحص الرقم — تعذّر الاتصال بالمزوّد")
        row = _only_row()
        self.assertEqual((row.outcome, row.error_code, row.attempts), ("failed", "connection", 3))
        self.assertEqual(row.message, result.classified.message)
        self.assertEqual(row.detail, "connection_error")

    def test_transient_then_success_records_success_with_attempts(self):
        adapter = FakeInfoAdapter.scripted(TransientError("timeout"), INFO)
        with patch("providers.service.time.sleep"), self.assertLogs(
            "providers.service", level="WARNING"
        ):
            result = self._run(adapter)
        self.assertTrue(result.ok)
        row = _only_row()
        self.assertEqual((row.outcome, row.attempts), ("success", 2))

    def test_login_path_transient_codes_are_not_retried(self):
        for code, cause in (("login_busy", "login_busy"), ("login_failed", "login_failed")):
            ProviderOperation.objects.all().delete()
            adapter = FakeInfoAdapter.scripted(TransientError(code))
            with patch("providers.service.time.sleep") as sleep, self.assertLogs(
                "providers.service", level="WARNING"
            ):
                result = self._run(adapter)
            self.assertEqual(len(adapter.calls), 1)
            sleep.assert_not_called()
            self.assertEqual(result.classified.error_code, cause)
            row = _only_row()
            self.assertEqual((row.error_code, row.attempts), (cause, 1))

    def test_exhausted_budget_is_connection_failure_with_no_attempt(self):
        adapter = FakeInfoAdapter.scripted(INFO)
        result = self._run(adapter, budget=0)
        self.assertFalse(result.ok)
        self.assertEqual(adapter.calls, [])
        self.assertEqual(result.classified.error_code, "connection")
        row = _only_row()
        self.assertEqual((row.error_code, row.attempts), ("connection", 0))
        self.assertEqual(row.detail, "sync_budget_exceeded")

    def test_budget_stops_retries(self):
        adapter = FakeInfoAdapter.scripted(*[TransientError("connection_error")] * 3)
        with patch("providers.service.time.sleep") as sleep, self.assertLogs(
            "providers.service", level="WARNING"
        ):
            self._run(adapter, budget=0.5)
        self.assertEqual(len(adapter.calls), 1)
        sleep.assert_not_called()
        self.assertEqual(_only_row().attempts, 1)

    def test_not_found_has_its_own_message_not_the_refresh_reject(self):
        adapter = FakeInfoAdapter.scripted(PermanentError("not_found"))
        with patch("providers.service.time.sleep") as sleep:
            result = self._run(adapter)
        sleep.assert_not_called()
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(result.classified.error_code, "not_found")
        self.assertEqual(result.classified.message, "فشل فحص الرقم — الرقم غير موجود لدى المزوّد")
        row = _only_row()
        self.assertEqual((row.outcome, row.error_code), ("failed", "not_found"))
        self.assertEqual(row.message, result.classified.message)

    def test_other_permanent_errors_are_classified(self):
        adapter = FakeInfoAdapter.scripted(PermanentError("config_error"))
        result = self._run(adapter)
        self.assertEqual(result.classified.error_code, "not_configured")
        self.assertEqual(_only_row().error_code, "not_configured")

    def test_auth_required_persisting_after_login_is_login_failure(self):
        adapter = FakeInfoAdapter.scripted(
            AuthRequired("expired"), AuthRequired("expired"), needs_login=True
        )
        with patch("providers.sessions.accounts.record_login"):
            result = self._run(adapter)
        self.assertEqual(result.classified.error_code, "login_failed")
        row = _only_row()
        self.assertEqual((row.error_code, row.attempts), ("login_failed", 1))

    def test_unexpected_error_is_internal_and_does_not_escape(self):
        adapter = FakeInfoAdapter.scripted(ValueError("boom token=secret123"))
        result = self._run(adapter)

        self.assertFalse(result.ok)
        self.assertEqual(result.classified.error_code, "internal")
        self.assertEqual(result.classified.message, "فشل فحص الرقم — خطأ داخلي")
        row = _only_row()
        self.assertEqual((row.outcome, row.error_code, row.attempts), ("failed", "internal", 1))
        self.assertIn("ValueError", row.detail)
        self.assertNotIn("secret123", row.detail)

    def test_detail_is_scrubbed(self):
        adapter = FakeInfoAdapter.scripted(TransientError("bad token=abc123"))
        with patch("providers.service.time.sleep"), self.assertLogs(
            "providers.service", level="WARNING"
        ):
            self._run(adapter)
        self.assertNotIn("abc123", _only_row().detail)

    def test_recording_failure_does_not_break_the_check(self):
        adapter = FakeInfoAdapter.scripted(INFO)
        with patch("providers.service.operations.record", side_effect=RuntimeError("db")):
            result = self._run(adapter)
        self.assertTrue(result.ok)

    def test_unknown_provider_raises_keyerror_and_records_nothing(self):
        with self.assertRaises(KeyError):
            service.number_info("nope", PHONE)
        self.assertFalse(ProviderOperation.objects.exists())


class RunNumberInfoJobTests(_Base):
    def _job(self, **overrides):
        params = dict(
            kind=ProviderJob.KIND_NUMBER_INFO,
            provider_key="fakeinfo",
            phone=PHONE,
            source="management",
            payload={"user_id": None, "sale_id": 5},
        )
        params.update(overrides)
        return jobs.create_job(**params)

    def _run_job(self, job, adapter_cls):
        with patch.dict(ADAPTERS, {"fakeinfo": adapter_cls}):
            run_number_info_job(str(job.pk))
        job.refresh_from_db()

    def test_budget_constant(self):
        self.assertEqual(NUMBER_INFO_BUDGET_SECONDS, 60)

    def test_done_job_holds_info_dict_that_round_trips(self):
        user = User.objects.create_user("ni_task_user", password="x")
        job = self._job(payload={"user_id": user.pk, "sale_id": 5})
        self._run_job(job, FakeInfoAdapter.scripted(INFO))

        self.assertEqual(job.status, "done")
        self.assertEqual(job.attempts, 1)
        self.assertEqual(job.result["status"], "ok")
        self.assertEqual(NumberInfo.from_dict(job.result["info"]), INFO)
        row = _only_row()
        self.assertEqual(row.service, "number_info")
        self.assertEqual(row.outcome, "success")
        self.assertEqual(row.source, "management")
        self.assertEqual(row.user_id, user.pk)
        self.assertEqual(row.sale_id, 5)
        self.assertEqual(row.job_id, job.pk)

    def test_failed_job_holds_classified_message_only(self):
        job = self._job()
        adapter = FakeInfoAdapter.scripted(PermanentError("not_found"))
        self._run_job(job, adapter)

        self.assertEqual(job.status, "failed")
        self.assertEqual(
            job.result,
            {
                "status": "error",
                "message": "فشل فحص الرقم — الرقم غير موجود لدى المزوّد",
                "error_code": "not_found",
            },
        )
        self.assertEqual(_only_row().error_code, "not_found")

    def test_provider_text_never_reaches_the_job_result(self):
        job = self._job()
        self._run_job(job, FakeInfoAdapter.scripted(ValueError("secret provider text")))
        self.assertEqual(job.status, "failed")
        self.assertNotIn("secret provider text", str(job.result))
        self.assertEqual(job.result["error_code"], "internal")

    def test_wrong_kind_job_is_untouched(self):
        job = self._job(kind=ProviderJob.KIND_REFRESH)
        adapter = FakeInfoAdapter.scripted(INFO)
        with self.assertLogs("providers.tasks", level="WARNING"):
            self._run_job(job, adapter)
        self.assertEqual((job.status, job.attempts, job.result), ("queued", 0, {}))
        self.assertEqual(adapter.calls, [])
        self.assertFalse(ProviderOperation.objects.exists())

    def test_already_running_job_is_untouched(self):
        job = self._job()
        jobs.mark_running(job)
        adapter = FakeInfoAdapter.scripted(INFO)
        self._run_job(job, adapter)
        self.assertEqual((job.status, job.attempts), ("running", 1))
        self.assertEqual(adapter.calls, [])
        self.assertFalse(ProviderOperation.objects.exists())

    def test_missing_or_malformed_job_id_is_ignored(self):
        with self.assertLogs("providers.tasks", level="WARNING"):
            run_number_info_job(str(uuid.uuid4()))
        with self.assertLogs("providers.tasks", level="WARNING"):
            run_number_info_job("not-a-uuid")
        self.assertFalse(ProviderOperation.objects.exists())

    def test_second_run_on_finished_job_writes_no_second_row(self):
        job = self._job()
        adapter = FakeInfoAdapter.scripted(INFO, INFO)
        self._run_job(job, adapter)
        self._run_job(job, adapter)
        self.assertEqual(job.status, "done")
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(ProviderOperation.objects.count(), 1)

    def test_unknown_provider_fails_the_job_without_raising_or_recording(self):
        job = self._job(provider_key="nope")
        with self.assertLogs("providers.tasks", level="ERROR"):
            run_number_info_job(str(job.pk))
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.result["status"], "error")
        self.assertEqual(job.result["error_code"], "internal")
        self.assertFalse(ProviderOperation.objects.exists())

    def test_service_crash_is_generic_failure_and_adds_no_row(self):
        job = self._job()
        with patch("providers.tasks.service.number_info", side_effect=RuntimeError("x")), \
                self.assertLogs("providers.tasks", level="ERROR"):
            run_number_info_job(str(job.pk))
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.result["message"], "فشل فحص الرقم — خطأ داخلي")
        self.assertFalse(ProviderOperation.objects.exists())

    def test_budget_and_ids_are_passed_to_the_service(self):
        job = self._job(payload={"user_id": 3, "sale_id": 9})
        with patch("providers.tasks.service.number_info") as number_info:
            number_info.return_value = service.NumberInfoResult(
                info=INFO, classified=service.operations.success("number_info")
            )
            run_number_info_job(str(job.pk))
        number_info.assert_called_once_with(
            "fakeinfo",
            PHONE,
            NUMBER_INFO_BUDGET_SECONDS,
            source="management",
            user_id=3,
            sale_id=9,
            job_id=job.pk,
        )

    def test_stale_connection_is_closed_only_when_not_eager(self):
        job = self._job()
        with patch("providers.tasks.close_old_connections") as close, self.settings(
            CELERY_TASK_ALWAYS_EAGER=False
        ):
            self._run_job(job, FakeInfoAdapter.scripted(INFO))
        close.assert_called()
        job = self._job()
        with patch("providers.tasks.close_old_connections") as close, self.settings(
            CELERY_TASK_ALWAYS_EAGER=True
        ):
            self._run_job(job, FakeInfoAdapter.scripted(INFO))
        close.assert_not_called()


class NumberInfoRetentionTests(TestCase):
    def test_purge_deletes_old_number_info_jobs_and_keeps_recent_ones(self):
        old = jobs.create_job(
            kind="number_info", provider_key="sky", phone=PHONE, source="employee"
        )
        recent = jobs.create_job(
            kind="number_info", provider_key="sky", phone=PHONE, source="employee"
        )
        ProviderJob.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - dt.timedelta(days=31)
        )

        deleted = purge_old_jobs()

        self.assertEqual(deleted, 1)
        self.assertFalse(ProviderJob.objects.filter(pk=old.pk).exists())
        self.assertTrue(ProviderJob.objects.filter(pk=recent.pk).exists())

    def test_purge_still_leaves_other_kinds_alone(self):
        job = jobs.create_job(kind="charge", provider_key="sky", phone=PHONE, source="employee")
        ProviderJob.objects.filter(pk=job.pk).update(
            created_at=timezone.now() - dt.timedelta(days=365)
        )
        purge_old_jobs()
        self.assertTrue(ProviderJob.objects.filter(pk=job.pk).exists())
