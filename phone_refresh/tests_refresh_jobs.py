"""Refresh job queue: precheck/complete split, submit/poll/wait, Celery task."""
from __future__ import annotations

import datetime as dt
import threading
import time
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from kombu.exceptions import OperationalError

from phone_refresh.models import (
    CustomerMessage,
    RefreshLog,
    RefreshSource,
    SystemSettings,
)
from phone_refresh.services import refresh_jobs
from phone_refresh.services.refresh_jobs import (
    job_payload,
    submit_refresh,
    wait_for_job,
)
from phone_refresh.services.refresh_service import (
    precheck,
    refresh_phone,
    result_payload,
)
from phone_refresh.tasks import (
    WORKER_REFRESH_BUDGET_SECONDS,
    reap_stuck_jobs,
    run_refresh_job,
)
from providers import jobs
from providers.redis_conn import get_redis
from providers.contracts import RawResponse
from providers.models import ProviderJob

PHONE = "0529000000"
SKY_REFRESHED = {"reason": "success"}
PROVIDER_REFRESH = "providers.service.refresh"


def _raw(data: dict = SKY_REFRESHED, text: str = "") -> RawResponse:
    return RawResponse(text=text, json=data, status_code=200)


class _JobTestCase(TestCase):
    def setUp(self):
        s = SystemSettings.get()
        s.db_precheck_enabled = False
        s.default_provider = "sky"
        s.show_last_refresh = False
        s.save()

    def submit(self, phone=PHONE, **kwargs):
        with self.captureOnCommitCallbacks(execute=True):
            return submit_refresh(phone, ip="1.2.3.4", source=RefreshSource.WEB, **kwargs)


class SubmitAndTaskTests(_JobTestCase):
    @patch(PROVIDER_REFRESH, return_value=_raw())
    def test_submit_returns_job_and_task_completes_with_refreshed_payload(self, refresh):
        ticket = self.submit()
        self.assertIsNotNone(ticket.job_id)
        self.assertIsNone(ticket.payload)
        self.assertEqual(job_payload(ticket.job_id)["status"], "refreshed")
        self.assertEqual(RefreshLog.objects.count(), 1)
        job = ProviderJob.objects.get(pk=ticket.job_id)
        self.assertEqual(job.status, "done")
        self.assertEqual(job.phone, PHONE)
        self.assertEqual(job.attempts, 1)
        refresh.assert_called_once_with(
            "sky", PHONE, budget=WORKER_REFRESH_BUDGET_SECONDS
        )

    def test_precheck_short_circuit_returns_payload_without_job(self):
        ticket = self.submit("123")
        self.assertIsNone(ticket.job_id)
        self.assertEqual(ticket.payload["status"], "not_found")
        self.assertEqual(ProviderJob.objects.count(), 0)

    @override_settings(REFRESH_ASYNC_ENABLED=False)
    @patch(PROVIDER_REFRESH, return_value=_raw())
    def test_async_disabled_runs_sync(self, refresh):
        ticket = self.submit()
        self.assertIsNone(ticket.job_id)
        self.assertEqual(ticket.payload["status"], "refreshed")
        self.assertEqual(ProviderJob.objects.count(), 0)
        refresh.assert_called_once_with("sky", PHONE)

    @patch(PROVIDER_REFRESH, return_value=_raw())
    def test_submit_reuses_inflight_job(self, refresh):
        first = refresh_jobs.submit_refresh(PHONE, ip=None, source=RefreshSource.WEB)
        second = refresh_jobs.submit_refresh(PHONE, ip=None, source=RefreshSource.WEB)
        self.assertEqual(first.job_id, second.job_id)
        self.assertEqual(ProviderJob.objects.count(), 1)
        refresh.assert_not_called()  # on_commit never ran inside TestCase

    def test_sms_submit_with_notify_does_not_join_web_job(self):
        web = refresh_jobs.submit_refresh(PHONE, ip=None, source=RefreshSource.WEB)
        sms = refresh_jobs.submit_refresh(
            PHONE, ip=None, source=RefreshSource.SMS, notify={"sms_inbound_id": 5}
        )
        self.assertNotEqual(web.job_id, sms.job_id)
        self.assertEqual(
            ProviderJob.objects.get(pk=sms.job_id).payload["notify"],
            {"sms_inbound_id": 5},
        )

    def test_forced_provider_submit_gets_its_own_job(self):
        web = refresh_jobs.submit_refresh(PHONE, ip=None, source=RefreshSource.WEB)
        admin = refresh_jobs.submit_refresh(
            PHONE, ip=None, source=RefreshSource.WEB, forced_provider="sky"
        )
        self.assertNotEqual(web.job_id, admin.job_id)
        self.assertEqual(ProviderJob.objects.get(pk=admin.job_id).payload["forced_provider"], "sky")

    def test_internal_test_submit_gets_its_own_job(self):
        web = refresh_jobs.submit_refresh(PHONE, ip=None, source=RefreshSource.WEB)
        test = refresh_jobs.submit_refresh(
            PHONE, ip=None, source=RefreshSource.INTERNAL_TEST, internal_test=True
        )
        self.assertNotEqual(web.job_id, test.job_id)

    def test_plain_submit_does_not_latch_onto_special_job(self):
        refresh_jobs.submit_refresh(
            PHONE, ip=None, source=RefreshSource.SMS, notify={"sms_inbound_id": 5}
        )
        refresh_jobs.submit_refresh(
            PHONE, ip=None, source=RefreshSource.WEB, forced_provider="sky"
        )
        plain = refresh_jobs.submit_refresh(PHONE, ip=None, source=RefreshSource.WEB)
        self.assertEqual(ProviderJob.objects.count(), 3)
        job = ProviderJob.objects.get(pk=plain.job_id)
        self.assertFalse(job.payload["notify"])
        self.assertFalse(job.payload["forced_provider"])
        # ...but a second plain submit now joins that plain job.
        again = refresh_jobs.submit_refresh(PHONE, ip=None, source=RefreshSource.WEB)
        self.assertEqual(again.job_id, plain.job_id)

    def test_job_source_is_normalised(self):
        ticket = refresh_jobs.submit_refresh(PHONE, ip=None, source="bogus")
        self.assertEqual(ProviderJob.objects.get(pk=ticket.job_id).source, RefreshSource.WEB)
        legacy = refresh_jobs.submit_refresh(
            PHONE, ip=None, source=RefreshSource.LEGACY, notify={"x": 1}
        )
        self.assertEqual(ProviderJob.objects.get(pk=legacy.job_id).source, RefreshSource.WEB)

    def test_submit_still_creates_job_when_redis_lock_unavailable(self):
        with patch(
            "phone_refresh.services.refresh_jobs.get_redis",
            side_effect=ConnectionError("redis down"),
        ), self.assertLogs("phone_refresh.services.refresh_jobs", level="WARNING"):
            ticket = refresh_jobs.submit_refresh(PHONE, ip=None, source=RefreshSource.WEB)
        self.assertIsNotNone(ticket.job_id)
        self.assertEqual(ProviderJob.objects.count(), 1)

    @patch(PROVIDER_REFRESH, return_value=_raw())
    def test_stored_job_payload_keeps_context_and_notify(self, refresh):
        ticket = refresh_jobs.submit_refresh(
            PHONE,
            ip="9.9.9.9",
            source=RefreshSource.SMS,
            notify={"sms_inbound_id": 7},
        )
        job = ProviderJob.objects.get(pk=ticket.job_id)
        self.assertEqual(job.source, RefreshSource.SMS)
        self.assertEqual(job.payload["ip"], "9.9.9.9")
        self.assertEqual(job.payload["notify"], {"sms_inbound_id": 7})
        self.assertFalse(job.payload["internal_test"])

    @patch(PROVIDER_REFRESH, return_value=_raw())
    def test_worker_log_uses_job_source_and_ip(self, refresh):
        self.submit()
        log = RefreshLog.objects.get()
        self.assertEqual(log.source, RefreshSource.WEB)
        self.assertEqual(log.ip, "1.2.3.4")

    @patch(
        "phone_refresh.services.refresh_jobs.run_refresh_job.delay",
        side_effect=OperationalError("broker down"),
    )
    def test_enqueue_failure_returns_busy_payload_and_fails_job(self, delay):
        # Outside a transaction on_commit fires immediately, as in a web request.
        with patch(
            "phone_refresh.services.refresh_jobs.transaction.on_commit",
            side_effect=lambda fn: fn(),
        ), self.assertLogs("phone_refresh.services.refresh_jobs", level="ERROR"):
            ticket = submit_refresh(PHONE, ip=None, source=RefreshSource.WEB)
        self.assertIsNone(ticket.job_id)
        self.assertEqual(ticket.payload["status"], "busy")
        self.assertEqual(
            ticket.payload["message"]["body"], "الخدمة مشغولة حالياً، حاول بعد قليل."
        )
        log = RefreshLog.objects.get()
        self.assertEqual(log.status.code, "busy")
        self.assertEqual(log.raw_excerpt, "")
        self.assertEqual(ProviderJob.objects.get().status, "failed")

    @patch(
        "phone_refresh.services.refresh_jobs.run_refresh_job.delay",
        side_effect=RuntimeError("unexpected broker bug"),
    )
    def test_enqueue_generic_exception_still_fails_job_with_busy(self, delay):
        with patch(
            "phone_refresh.services.refresh_jobs.transaction.on_commit",
            side_effect=lambda fn: fn(),
        ), self.assertLogs("phone_refresh.services.refresh_jobs", level="ERROR") as logs:
            ticket = submit_refresh(PHONE, ip=None, source=RefreshSource.WEB)
        self.assertEqual(ticket.payload["status"], "busy")
        self.assertEqual(ProviderJob.objects.get().status, "failed")
        self.assertIn("Traceback", logs.output[0])

    @patch(
        "phone_refresh.services.refresh_jobs.run_refresh_job.delay",
        side_effect=OSError("redis unreachable"),
    )
    def test_enqueue_failure_inside_transaction_is_visible_by_polling(self, delay):
        with self.assertLogs("phone_refresh.services.refresh_jobs", level="ERROR"):
            ticket = self.submit()  # on_commit runs after submit returns
        self.assertEqual(job_payload(ticket.job_id)["status"], "busy")


class JobPayloadTests(_JobTestCase):
    def _queued_job(self, age_seconds=0):
        ticket = refresh_jobs.submit_refresh(PHONE, ip=None, source=RefreshSource.WEB)
        ProviderJob.objects.filter(pk=ticket.job_id).update(
            created_at=timezone.now() - dt.timedelta(seconds=age_seconds)
        )
        return ticket.job_id

    def test_job_payload_processing_then_deadline_message(self):
        job_id = self._queued_job(age_seconds=10)
        self.assertEqual(
            job_payload(job_id), {"status": "processing", "job": job_id}
        )
        job_id = self._queued_job(age_seconds=91)
        payload = job_payload(job_id)
        self.assertEqual(payload["status"], "processing")
        self.assertTrue(payload["final"])
        self.assertEqual(
            payload["message"]["body"], "طلبك قيد المعالجة، أعد المحاولة بعد دقيقة."
        )

    def test_deadline_uses_admin_edited_message_and_wait_setting(self):
        SystemSettings.objects.filter(pk=1).update(refresh_wait_seconds=30)
        msg = CustomerMessage.objects.get(status__code="processing")
        msg.body = "انتظر"
        msg.save()
        job_id = self._queued_job(age_seconds=31)
        payload = job_payload(job_id)
        self.assertTrue(payload["final"])
        self.assertEqual(payload["message"]["body"], "انتظر")

    def test_unknown_job_returns_generic_error(self):
        expected = {
            "status": "error",
            "message": {"title": "خطأ", "body": "انتهت صلاحية الطلب، أعد المحاولة."},
        }
        self.assertEqual(job_payload("00000000-0000-0000-0000-000000000000"), expected)
        self.assertEqual(job_payload("not-a-uuid"), expected)

    def test_done_job_returns_stored_result(self):
        job_id = self._queued_job()
        ProviderJob.objects.filter(pk=job_id).update(
            status="done", result={"status": "wait", "message": {"title": "t", "body": "b"}}
        )
        self.assertEqual(job_payload(job_id)["status"], "wait")

    def test_failed_job_returns_stored_error_payload(self):
        job_id = self._queued_job()
        ProviderJob.objects.filter(pk=job_id).update(
            status="failed", result={"status": "error", "message": {"title": "x", "body": "y"}}
        )
        self.assertEqual(job_payload(job_id)["status"], "error")


class WaitForJobTests(_JobTestCase):
    @patch(PROVIDER_REFRESH, return_value=_raw())
    def test_eager_mode_runs_queued_job_inline(self, refresh):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            ticket = refresh_jobs.submit_refresh(
                PHONE, ip=None, source=RefreshSource.WEB
            )
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(ProviderJob.objects.get().status, "queued")

        payload = wait_for_job(ticket.job_id, timeout_seconds=5)

        self.assertEqual(payload["status"], "refreshed")
        refresh.assert_called_once()
        # The deferred enqueue now fires; mark_running must make it a no-op.
        for callback in callbacks:
            callback()
        refresh.assert_called_once()
        self.assertEqual(RefreshLog.objects.count(), 1)
        self.assertEqual(ProviderJob.objects.get().attempts, 1)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_non_eager_polls_until_timeout_and_returns_last_payload(self):
        ticket = refresh_jobs.submit_refresh(PHONE, ip=None, source=RefreshSource.WEB)
        clock = {"now": 0.0}

        def fake_sleep(seconds):
            clock["now"] += seconds

        with patch(PROVIDER_REFRESH) as refresh, patch(
            "phone_refresh.services.refresh_jobs.time.monotonic",
            side_effect=lambda: clock["now"],
        ), patch(
            "phone_refresh.services.refresh_jobs.time.sleep", side_effect=fake_sleep
        ) as sleep:
            payload = wait_for_job(ticket.job_id, timeout_seconds=2)
        refresh.assert_not_called()
        self.assertEqual(payload, {"status": "processing", "job": ticket.job_id})
        self.assertEqual(sleep.call_count, 4)  # 0.5 s polls for 2 s

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_non_eager_returns_as_soon_as_job_finishes(self):
        ticket = refresh_jobs.submit_refresh(PHONE, ip=None, source=RefreshSource.WEB)

        def finish(_):
            ProviderJob.objects.filter(pk=ticket.job_id).update(
                status="done", result={"status": "wait", "message": {"title": "", "body": ""}}
            )

        with patch("phone_refresh.services.refresh_jobs.time.sleep", side_effect=finish):
            payload = wait_for_job(ticket.job_id, timeout_seconds=30)
        self.assertEqual(payload["status"], "wait")


class RunRefreshJobTests(_JobTestCase):
    def test_missing_job_is_logged_and_ignored(self):
        with self.assertLogs("phone_refresh.tasks", level="WARNING"):
            run_refresh_job("00000000-0000-0000-0000-000000000000")

    @patch(PROVIDER_REFRESH, return_value=_raw())
    def test_already_running_job_is_skipped(self, refresh):
        ticket = refresh_jobs.submit_refresh(PHONE, ip=None, source=RefreshSource.WEB)
        ProviderJob.objects.filter(pk=ticket.job_id).update(status="running")
        run_refresh_job(ticket.job_id)
        refresh.assert_not_called()

    @patch(PROVIDER_REFRESH, side_effect=RuntimeError("boom secret=abc"))
    def test_provider_crash_yields_generic_error_payload(self, refresh):
        ticket = refresh_jobs.submit_refresh(PHONE, ip=None, source=RefreshSource.WEB)
        with self.assertLogs("phone_refresh.tasks", level="ERROR"):
            run_refresh_job(ticket.job_id)
        job = ProviderJob.objects.get(pk=ticket.job_id)
        self.assertEqual(job.status, "done")
        self.assertEqual(job.result["status"], "error")
        self.assertNotIn("boom", str(job.result))

    @patch("phone_refresh.tasks.complete", side_effect=RuntimeError("db exploded"))
    @patch(PROVIDER_REFRESH, return_value=_raw())
    def test_unexpected_exception_marks_job_failed_with_error_payload(self, refresh, complete):
        ticket = refresh_jobs.submit_refresh(PHONE, ip=None, source=RefreshSource.WEB)
        with self.assertLogs("phone_refresh.tasks", level="ERROR"):
            run_refresh_job(ticket.job_id)
        job = ProviderJob.objects.get(pk=ticket.job_id)
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.result["status"], "error")
        self.assertNotIn("db exploded", str(job.result))
        self.assertEqual(job_payload(ticket.job_id)["status"], "error")

    @patch("phone_refresh.tasks._notify")
    @patch(PROVIDER_REFRESH, return_value=_raw())
    def test_notify_hook_called_with_job_and_result(self, refresh, notify):
        ticket = refresh_jobs.submit_refresh(
            PHONE, ip=None, source=RefreshSource.SMS, notify={"sms_inbound_id": 3}
        )
        run_refresh_job(ticket.job_id)
        (job, result), _ = notify.call_args
        self.assertEqual(job.pk.hex, ticket.job_id.replace("-", ""))
        self.assertEqual(result["status"], "refreshed")

    @patch("phone_refresh.tasks._notify", side_effect=RuntimeError("sms down"))
    @patch(PROVIDER_REFRESH, return_value=_raw())
    def test_notify_failure_does_not_fail_done_job(self, refresh, notify):
        ticket = refresh_jobs.submit_refresh(PHONE, ip=None, source=RefreshSource.SMS)
        with self.assertLogs("phone_refresh.tasks", level="ERROR"):
            run_refresh_job(ticket.job_id)
        self.assertEqual(ProviderJob.objects.get(pk=ticket.job_id).status, "done")


class ServiceSplitTests(_JobTestCase):
    @patch(PROVIDER_REFRESH, return_value=_raw())
    def test_refresh_phone_sync_signature_unchanged(self, refresh):
        result = refresh_phone(
            PHONE,
            ip="1.1.1.1",
            internal_test=True,
            forced_provider="sky",
            bypass_provider_off=True,
            source=RefreshSource.INTERNAL_TEST,
        )
        self.assertEqual(result.status.code, "refreshed")
        refresh.assert_called_once_with("sky", PHONE)

    @patch(PROVIDER_REFRESH)
    def test_raw_body_is_scrubbed(self, refresh):
        refresh.return_value = _raw(
            None, text='{"jwtToken": "abc", "message": "unknown thing"}'
        )
        result = refresh_phone(PHONE)
        log = RefreshLog.objects.get()
        self.assertNotIn("abc", log.raw_body)
        self.assertNotIn("abc", log.raw_excerpt)
        self.assertNotIn("abc", result.raw_body_full)
        self.assertIn("unknown thing", log.raw_body)

    def test_precheck_returns_provider_key_when_clear(self):
        immediate, key = precheck(PHONE, ip=None, source=RefreshSource.WEB)
        self.assertIsNone(immediate)
        self.assertEqual(key, "sky")

    def test_precheck_returns_logged_result_for_service_off(self):
        SystemSettings.objects.filter(pk=1).update(service_enabled=False)
        immediate, key = precheck(PHONE, ip=None, source=RefreshSource.WEB)
        self.assertEqual(immediate.status.code, "service_off")
        self.assertEqual(RefreshLog.objects.count(), 1)

    def test_precheck_unknown_provider_is_no_provider_error(self):
        immediate, _ = precheck(
            PHONE,
            ip=None,
            source=RefreshSource.WEB,
            internal_test=True,
            forced_provider="nope",
            bypass_provider_off=True,
        )
        self.assertEqual(immediate.error, "no_provider:nope")

    @patch(PROVIDER_REFRESH, return_value=_raw())
    def test_result_payload_shape(self, refresh):
        result = refresh_phone(PHONE)
        payload = result_payload(result)
        self.assertEqual(set(payload), {"status", "message"})
        self.assertEqual(set(payload["message"]), {"title", "body"})
        SystemSettings.objects.filter(pk=1).update(show_last_refresh=True)
        RefreshLog.objects.update(created_at=timezone.now() - dt.timedelta(hours=7))
        again = refresh_phone(PHONE)
        full = result_payload(again)
        self.assertIn("last_refresh_at", full)
        self.assertIn("seconds_since_last_refresh", full)


class RefreshWaitSettingTests(TestCase):
    def test_default_is_90_and_statuses_are_seeded(self):
        self.assertEqual(SystemSettings.get().refresh_wait_seconds, 90)
        self.assertEqual(
            CustomerMessage.objects.get(status__code="busy").body,
            "الخدمة مشغولة حالياً، حاول بعد قليل.",
        )
        self.assertEqual(
            CustomerMessage.objects.get(status__code="processing").status.label,
            "قيد المعالجة",
        )

    def test_form_exposes_and_validates_wait_seconds(self):
        from phone_refresh.forms import SystemSettingsForm

        instance = SystemSettings.get()
        data = {
            "service_enabled": "on",
            "db_precheck_enabled": "on",
            "show_last_refresh": "on",
            "cooldown_seconds": "21600",
            "refresh_wait_seconds": "120",
            "default_provider": "",
        }
        form = SystemSettingsForm(data, instance=instance)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(SystemSettings.get().refresh_wait_seconds, 120)
        bad = SystemSettingsForm({**data, "refresh_wait_seconds": "3"}, instance=instance)
        self.assertFalse(bad.is_valid())
        self.assertIn("refresh_wait_seconds", bad.errors)


class ErrorScrubbingTests(_JobTestCase):
    @patch(PROVIDER_REFRESH)
    def test_raw_error_is_scrubbed_before_persisting(self, refresh):
        refresh.return_value = RawResponse(
            text="", json=None, status_code=0, error="login failed password=hunter2"
        )
        result = refresh_phone(PHONE)
        self.assertNotIn("hunter2", result.error)
        self.assertIn("password=***", result.error)


class ReapStuckJobsTests(_JobTestCase):
    def _job(self, status, *, created_ago=0, started_ago=None, **extra):
        job = jobs.create_job(
            kind="refresh", provider_key="sky", phone=PHONE, source="web"
        )
        now = timezone.now()
        ProviderJob.objects.filter(pk=job.pk).update(
            status=status,
            created_at=now - dt.timedelta(seconds=created_ago),
            started_at=(now - dt.timedelta(seconds=started_ago)) if started_ago is not None else None,
            **extra,
        )
        return str(job.pk)

    def test_fails_old_running_and_old_queued_jobs_only(self):
        stuck_running = self._job("running", created_ago=900, started_ago=700)
        stuck_queued = self._job("queued", created_ago=700)
        fresh_running = self._job("running", created_ago=900, started_ago=60)
        fresh_queued = self._job("queued", created_ago=60)
        done = self._job("done", created_ago=900, result={"status": "refreshed"})

        with self.assertLogs("phone_refresh.tasks", level="WARNING"):
            self.assertEqual(reap_stuck_jobs(), 2)

        status = lambda pk: ProviderJob.objects.get(pk=pk).status  # noqa: E731
        self.assertEqual(status(stuck_running), "failed")
        self.assertEqual(status(stuck_queued), "failed")
        self.assertEqual(status(fresh_running), "running")
        self.assertEqual(status(fresh_queued), "queued")
        self.assertEqual(status(done), "done")
        failed = ProviderJob.objects.get(pk=stuck_running)
        self.assertEqual(failed.result["status"], "error")
        self.assertIsNotNone(failed.finished_at)
        self.assertEqual(job_payload(stuck_running)["status"], "error")

    def test_only_refresh_jobs_are_reaped(self):
        other = ProviderJob.objects.create(
            kind="charge", provider_key="sky", phone=PHONE, source="web", status="running"
        )
        ProviderJob.objects.filter(pk=other.pk).update(
            started_at=timezone.now() - dt.timedelta(hours=1)
        )
        self.assertEqual(reap_stuck_jobs(), 0)
        self.assertEqual(ProviderJob.objects.get(pk=other.pk).status, "running")

    @patch("phone_refresh.tasks._notify")
    def test_notifies_reaped_jobs(self, notify):
        job_id = self._job("running", created_ago=900, started_ago=700)
        with self.assertLogs("phone_refresh.tasks", level="WARNING"):
            reap_stuck_jobs()
        (job, result), _ = notify.call_args
        self.assertEqual(str(job.pk), job_id)
        self.assertEqual(result["status"], "error")

    def test_reaper_is_scheduled_every_five_minutes(self):
        from django.conf import settings

        entry = settings.CELERY_BEAT_SCHEDULE["phone-refresh-reap-stuck-jobs"]
        self.assertEqual(entry["task"], "phone_refresh.tasks.reap_stuck_jobs")
        self.assertEqual(entry["schedule"], 300.0)


class WorkerConnectionTests(_JobTestCase):
    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    @patch("phone_refresh.tasks.close_old_connections")
    @patch(PROVIDER_REFRESH, return_value=_raw())
    def test_connection_refreshed_before_final_write(self, refresh, close):
        ticket = refresh_jobs.submit_refresh(PHONE, ip=None, source=RefreshSource.WEB)
        order = []
        close.side_effect = lambda: order.append("close")
        with patch(
            "phone_refresh.tasks.jobs.mark_done",
            side_effect=lambda *a, **k: order.append("mark_done"),
        ):
            run_refresh_job(ticket.job_id)
        self.assertEqual(order, ["close", "mark_done"])

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    @patch("phone_refresh.tasks.close_old_connections")
    @patch("phone_refresh.tasks.complete", side_effect=RuntimeError("x"))
    @patch(PROVIDER_REFRESH, return_value=_raw())
    def test_connection_refreshed_before_failed_write(self, refresh, complete, close):
        ticket = refresh_jobs.submit_refresh(PHONE, ip=None, source=RefreshSource.WEB)
        with self.assertLogs("phone_refresh.tasks", level="ERROR"):
            run_refresh_job(ticket.job_id)
        close.assert_called_once()
        self.assertEqual(ProviderJob.objects.get(pk=ticket.job_id).status, "failed")


class ConcurrentSubmitTests(TransactionTestCase):
    def setUp(self):
        get_redis().flushall()
        s = SystemSettings.get()
        s.db_precheck_enabled = False
        s.default_provider = "sky"
        s.save()

    def test_simultaneous_submits_create_one_job(self):
        real_find = jobs.find_inflight

        def slow_find(*args, **kwargs):
            found = real_find(*args, **kwargs)
            time.sleep(0.3)  # widen the find -> create race window
            return found

        barrier = threading.Barrier(2)
        results, errors = [], []

        def submit():
            try:
                barrier.wait(timeout=5)
                results.append(
                    refresh_jobs.submit_refresh(PHONE, ip=None, source=RefreshSource.WEB)
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                connection.close()

        # Patched once here: patching from inside the threads would race.
        with patch(
            "phone_refresh.services.refresh_jobs.jobs.find_inflight", slow_find
        ), patch("phone_refresh.services.refresh_jobs.run_refresh_job.delay"):
            threads = [threading.Thread(target=submit) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=20)

        self.assertEqual(errors, [])
        self.assertEqual(ProviderJob.objects.count(), 1)
        self.assertEqual(len({t.job_id for t in results}), 1)
