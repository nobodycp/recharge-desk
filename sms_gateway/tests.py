import hashlib
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone


class SmsBaseTest(TestCase):
    """Clears the process-local caches that survive TestCase rollbacks.

    ``SmsGatewaySettings.load()`` memoizes the singleton in the locmem
    cache, and ``RefreshStatus.get_by_code`` memoizes on the class; both
    would otherwise bleed state between tests.
    """

    def setUp(self):
        super().setUp()
        cache.clear()
        RefreshStatus.clear_cache()

from phone_refresh.models import RefreshStatus
from sms_gateway.models import (
    InboundSms,
    OutboundSms,
    SmsAccessRule,
    SmsGatewayDevice,
    SmsGatewaySettings,
    SmsReplyPolicy,
)
from sms_gateway.services.refresh_client import SmsRefreshOutcome
from sms_gateway.services import outbox as outbox_service
from sms_gateway.validation import extract_number, is_valid_number

RAW_TOKEN = "test-device-token-123456"


def _make_device(**kwargs):
    defaults = dict(
        name="Main",
        phone_number="0595108208",
        token_hash=hashlib.sha256(RAW_TOKEN.encode()).hexdigest(),
        token_prefix=RAW_TOKEN[:8],
    )
    defaults.update(kwargs)
    return SmsGatewayDevice.objects.create(**defaults)


def _ok_outcome(*args, **kwargs):
    return SmsRefreshOutcome(status_code="refreshed", title="تم", body="تم التحديث", http_status=200)


class NumberExtractionTests(SmsBaseTest):
    def test_extracts_embedded_number(self):
        self.assertEqual(extract_number("حدثلي 0555544071 شكرا"), "0555544071")

    def test_normalizes_arabic_digits(self):
        self.assertEqual(extract_number("٠٥٥٥٥٤٤٠٧١"), "0555544071")

    def test_strips_separators(self):
        self.assertEqual(extract_number("055-554-4071"), "0555544071")

    def test_rejects_059(self):
        self.assertIsNone(extract_number("0595108208"))

    def test_validation_range(self):
        self.assertTrue(is_valid_number("0501234567"))
        self.assertFalse(is_valid_number("0591234567"))


class DeviceApiAuthTests(SmsBaseTest):
    def test_inbound_requires_token(self):
        resp = self.client.post(
            reverse("sms_gateway_api:api_inbound"),
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_inbound_rejects_bad_token(self):
        _make_device()
        resp = self.client.post(
            reverse("sms_gateway_api:api_inbound"),
            data='{"from":"0595108208","text":"hi"}',
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer wrong",
        )
        self.assertEqual(resp.status_code, 401)


@override_settings(REFRESH_ASYNC_ENABLED=False)  # these mock the synchronous refresh call
class EndToEndTests(SmsBaseTest):
    def setUp(self):
        super().setUp()
        self.device = _make_device()
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {RAW_TOKEN}"}

    @mock.patch("sms_gateway.services.processing.call_refresh_api", side_effect=_ok_outcome)
    def test_full_flow_inbound_to_delivery(self, _m):
        resp = self.client.post(
            reverse("sms_gateway_api:api_inbound"),
            data='{"from":"0595108208","text":"حدثلي 0555544071","device_msg_id":"m1"}',
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["state"], InboundSms.State.PROCESSED)
        self.assertEqual(body["extracted_number"], "0555544071")
        self.assertTrue(body["queued_reply"])

        ob = OutboundSms.objects.get()
        self.assertEqual(ob.to_number, "0595108208")  # reply to SENDER

        # Outbox poll claims it.
        out = self.client.get(reverse("sms_gateway_api:api_outbox"), **self.auth).json()
        self.assertEqual(len(out["messages"]), 1)
        self.assertIn("m1", out["delete_ids"])

        # Delivery marks sent + confirms deletion.
        self.client.post(
            reverse("sms_gateway_api:api_delivery"),
            data='{"sent":[%d],"deleted":["m1"]}' % ob.pk,
            content_type="application/json",
            **self.auth,
        )
        ob.refresh_from_db()
        self.assertEqual(ob.state, OutboundSms.State.SENT)
        inbound = InboundSms.objects.get(device_msg_id="m1")
        self.assertIsNotNone(inbound.delete_confirmed_at)

    @mock.patch("sms_gateway.services.processing.call_refresh_api", side_effect=_ok_outcome)
    def test_duplicate_ignored(self, _m):
        payload = '{"from":"0595108208","text":"0555544071","device_msg_id":"dup"}'
        self.client.post(reverse("sms_gateway_api:api_inbound"), data=payload,
                         content_type="application/json", **self.auth)
        resp = self.client.post(reverse("sms_gateway_api:api_inbound"), data=payload,
                                content_type="application/json", **self.auth)
        self.assertEqual(resp.json()["state"], InboundSms.State.DUPLICATE)
        self.assertEqual(InboundSms.objects.count(), 1)

    @mock.patch("sms_gateway.services.processing.call_refresh_api", side_effect=_ok_outcome)
    def test_test_number_no_refresh(self, m):
        s = SmsGatewaySettings.load()
        s.test_number = "0595108208"
        s.test_reply_message = "شغالة"
        s.save()
        resp = self.client.post(
            reverse("sms_gateway_api:api_inbound"),
            data='{"from":"0595108208","text":"anything"}',
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(resp.json()["state"], InboundSms.State.TEST)
        m.assert_not_called()
        self.assertEqual(OutboundSms.objects.get().body, "شغالة")

    @mock.patch("sms_gateway.services.processing.call_refresh_api", side_effect=_ok_outcome)
    def test_service_off_no_reply(self, _m):
        s = SmsGatewaySettings.load()
        s.service_enabled = False
        s.save()
        self.client.post(
            reverse("sms_gateway_api:api_inbound"),
            data='{"from":"0595108208","text":"0555544071"}',
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(OutboundSms.objects.count(), 0)

    @mock.patch("sms_gateway.services.processing.call_refresh_api", side_effect=_ok_outcome)
    def test_master_replies_off(self, _m):
        s = SmsGatewaySettings.load()
        s.replies_master_enabled = False
        s.save()
        self.client.post(
            reverse("sms_gateway_api:api_inbound"),
            data='{"from":"0595108208","text":"0555544071"}',
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(OutboundSms.objects.count(), 0)

    @mock.patch("sms_gateway.services.processing.call_refresh_api", side_effect=_ok_outcome)
    def test_per_status_reply_off(self, _m):
        status, _ = RefreshStatus.objects.get_or_create(code="refreshed", defaults={"label": "تم"})
        SmsReplyPolicy.objects.create(status=status, reply_enabled=False)
        self.client.post(
            reverse("sms_gateway_api:api_inbound"),
            data='{"from":"0595108208","text":"0555544071"}',
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(OutboundSms.objects.count(), 0)

    @mock.patch("sms_gateway.services.processing.call_refresh_api", side_effect=_ok_outcome)
    def test_blocked_sender(self, _m):
        SmsAccessRule.objects.create(value="0595108208", mode=SmsAccessRule.Mode.BLOCK)
        resp = self.client.post(
            reverse("sms_gateway_api:api_inbound"),
            data='{"from":"0595108208","text":"0555544071"}',
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(resp.json()["state"], InboundSms.State.BLOCKED)
        self.assertEqual(OutboundSms.objects.count(), 0)

    @mock.patch("sms_gateway.services.processing.call_refresh_api", side_effect=_ok_outcome)
    def test_sender_rate_limit(self, _m):
        s = SmsGatewaySettings.load()
        s.sender_max_messages = 1
        s.sender_window_minutes = 60
        s.save()
        for _ in range(2):
            self.client.post(
                reverse("sms_gateway_api:api_inbound"),
                data='{"from":"0595108208","text":"0555544071"}',
                content_type="application/json",
                **self.auth,
            )
        self.assertTrue(
            InboundSms.objects.filter(state=InboundSms.State.RATE_LIMITED).exists()
        )


class OutboxFailoverTests(SmsBaseTest):
    def setUp(self):
        super().setUp()
        self.device = _make_device()

    def test_claim_timeout_reclaims(self):
        ob = OutboundSms.objects.create(to_number="0555544071", body="x")
        claimed = outbox_service.claim_for_device(self.device)
        self.assertEqual(len(claimed), 1)
        ob.refresh_from_db()
        self.assertEqual(ob.state, OutboundSms.State.CLAIMED)
        # Force the claim to be stale.
        OutboundSms.objects.filter(pk=ob.pk).update(
            claimed_at=timezone.now() - timedelta(seconds=10_000)
        )
        outbox_service._reclaim_expired(SmsGatewaySettings.load())
        ob.refresh_from_db()
        self.assertEqual(ob.state, OutboundSms.State.QUEUED)

    def test_failure_dead_letters_after_max_attempts(self):
        ob = OutboundSms.objects.create(to_number="0555544071", body="x", max_attempts=1)
        outbox_service.claim_for_device(self.device)  # attempts -> 1
        outbox_service.mark_delivery(
            self.device, sent_ids=[], failed=[{"id": ob.pk, "error": "no balance"}],
            deleted_device_msg_ids=[],
        )
        ob.refresh_from_db()
        self.assertEqual(ob.state, OutboundSms.State.DEAD_LETTER)

    def test_auto_pause_after_consecutive_failures(self):
        s = SmsGatewaySettings.load()
        s.auto_pause_threshold = 1
        s.save()
        ob = OutboundSms.objects.create(to_number="0555544071", body="x", max_attempts=5)
        outbox_service.claim_for_device(self.device)
        outbox_service.mark_delivery(
            self.device, sent_ids=[], failed=[{"id": ob.pk, "error": "fail"}],
            deleted_device_msg_ids=[],
        )
        self.device.refresh_from_db()
        self.assertIsNotNone(self.device.auto_paused_at)
        self.assertFalse(self.device.is_sendable)


class SimulatorTests(SmsBaseTest):
    @mock.patch("sms_gateway.services.processing.call_refresh_api", side_effect=_ok_outcome)
    def test_simulator_does_not_persist(self, _m):
        from sms_gateway.services.processing import process_inbound

        result = process_inbound(from_number="0595108208", raw_text="0555544071", simulate=True)
        self.assertEqual(result.state, InboundSms.State.PROCESSED)
        self.assertEqual(result.reply_text, "تم\nتم التحديث")
        self.assertEqual(InboundSms.objects.count(), 0)
        self.assertEqual(OutboundSms.objects.count(), 0)


class PurgeCommandTests(SmsBaseTest):
    def test_purge_old_logs(self):
        from django.core.management import call_command

        s = SmsGatewaySettings.load()
        s.log_retention_days = 30
        s.save()
        old = InboundSms.objects.create(from_number="0555544071", raw_text="x")
        InboundSms.objects.filter(pk=old.pk).update(
            received_at=timezone.now() - timedelta(days=60)
        )
        InboundSms.objects.create(from_number="0555544072", raw_text="y")
        call_command("sms_purge_logs")
        self.assertEqual(InboundSms.objects.count(), 1)


PROVIDER_REFRESH = "providers.service.refresh"
SENDER = "0595108208"
REFRESHED_NUMBER = "0555544071"


class AsyncInboundTests(SmsBaseTest):
    """In-process refresh runs as a job; the reply is queued on completion."""

    def setUp(self):
        super().setUp()
        from phone_refresh.models import SystemSettings

        s = SystemSettings.get()
        s.db_precheck_enabled = False
        s.default_provider = "sky"
        s.show_last_refresh = False
        s.save()
        self.device = _make_device()

    def _inbound(self, **kwargs):
        from sms_gateway.services.processing import process_inbound

        return process_inbound(
            from_number=SENDER, raw_text=REFRESHED_NUMBER, device=self.device, **kwargs
        )

    def _refreshing_inbound(self):
        return InboundSms.objects.create(
            device=self.device,
            from_number=SENDER,
            raw_text=REFRESHED_NUMBER,
            extracted_number=REFRESHED_NUMBER,
            state=InboundSms.State.REFRESHING,
        )

    @mock.patch(PROVIDER_REFRESH)
    def test_inbound_enqueues_and_reply_created_on_completion(self, refresh):
        from providers.contracts import RawResponse
        from providers.models import ProviderJob

        refresh.return_value = RawResponse(
            text="", json={"reason": "success"}, status_code=200
        )
        with self.captureOnCommitCallbacks(execute=True):
            result = self._inbound(device_msg_id="m1")
            # Returned before the job ran: the gateway's HTTP call is not held.
            self.assertEqual(result.state, InboundSms.State.REFRESHING)
            self.assertEqual(result.extracted_number, REFRESHED_NUMBER)
            self.assertIsNone(result.outbound_id)
            self.assertEqual(OutboundSms.objects.count(), 0)

        job = ProviderJob.objects.get()
        self.assertEqual(job.payload["notify"], {"sms_inbound_id": result.inbound_id})
        self.assertEqual(job.status, "done")
        message = job.result["message"]
        outbound = OutboundSms.objects.get()
        self.assertEqual(outbound.to_number, SENDER)  # reply goes to the SENDER
        self.assertEqual(outbound.related_inbound_id, result.inbound_id)
        self.assertEqual(outbound.body, "\n".join([message["title"], message["body"]]))
        self.assertEqual(job.result["status"], "refreshed")
        inbound = InboundSms.objects.get(pk=result.inbound_id)
        self.assertEqual(inbound.state, InboundSms.State.PROCESSED)
        self.assertEqual(inbound.extracted_number, REFRESHED_NUMBER)
        self.assertEqual(inbound.reply_text, outbound.body)
        self.assertTrue(inbound.delete_requested)
        self.assertIsNotNone(inbound.processed_at)

    @mock.patch(PROVIDER_REFRESH)
    def test_inbound_http_returns_refreshing_and_reply_is_polled_from_outbox(self, refresh):
        from providers.contracts import RawResponse

        refresh.return_value = RawResponse(
            text="", json={"reason": "success"}, status_code=200
        )
        auth = {"HTTP_AUTHORIZATION": f"Bearer {RAW_TOKEN}"}
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(
                reverse("sms_gateway_api:api_inbound"),
                data='{"from":"%s","text":"%s","device_msg_id":"m9"}'
                % (SENDER, REFRESHED_NUMBER),
                content_type="application/json",
                **auth,
            )
        self.assertEqual(resp.json()["state"], InboundSms.State.REFRESHING)
        self.assertFalse(resp.json()["queued_reply"])
        out = self.client.get(reverse("sms_gateway_api:api_outbox"), **auth).json()
        self.assertEqual(len(out["messages"]), 1)
        self.assertEqual(out["messages"][0]["to"], SENDER)

    def test_complete_inbound_is_idempotent(self):
        from sms_gateway.services.processing import complete_inbound

        inbound = self._refreshing_inbound()
        complete_inbound(inbound.pk, "refreshed", "تم", "تم التحديث")
        complete_inbound(inbound.pk, "error", "خطأ", "فشل")
        outbound = OutboundSms.objects.get()
        self.assertEqual(outbound.body, "تم\nتم التحديث")
        inbound.refresh_from_db()
        self.assertEqual(inbound.state, InboundSms.State.PROCESSED)
        self.assertEqual(inbound.reply_text, "تم\nتم التحديث")

    def test_complete_inbound_ignores_inbound_not_refreshing(self):
        from sms_gateway.services.processing import complete_inbound

        inbound = self._refreshing_inbound()
        InboundSms.objects.filter(pk=inbound.pk).update(state=InboundSms.State.IGNORED)
        complete_inbound(inbound.pk, "refreshed", "تم", "تم التحديث")
        complete_inbound(999999, "refreshed", "تم", "تم التحديث")  # unknown id
        self.assertEqual(OutboundSms.objects.count(), 0)
        inbound.refresh_from_db()
        self.assertEqual(inbound.state, InboundSms.State.IGNORED)

    def test_complete_inbound_race_loser_sends_nothing(self):
        """A completer that lost the claim to a concurrent one queues no reply."""
        from sms_gateway.services import processing

        inbound = self._refreshing_inbound()
        real_reply_for = processing._reply_for

        def reply_after_rival_claims(*args, **kwargs):
            # The reaper / a late worker finishes the inbound while we build ours.
            InboundSms.objects.filter(pk=inbound.pk).update(
                state=InboundSms.State.PROCESSED
            )
            return real_reply_for(*args, **kwargs)

        with mock.patch.object(processing, "_reply_for", side_effect=reply_after_rival_claims):
            processing.complete_inbound(inbound.pk, "refreshed", "تم", "تم التحديث")
        self.assertEqual(OutboundSms.objects.count(), 0)

    def test_complete_inbound_respects_policy_and_daily_cap(self):
        from sms_gateway.services.processing import complete_inbound

        s = SmsGatewaySettings.load()
        s.global_daily_cap = 1
        s.save()
        cache.clear()
        OutboundSms.objects.create(to_number="0550000000", body="earlier")
        inbound = self._refreshing_inbound()
        complete_inbound(inbound.pk, "refreshed", "تم", "تم التحديث")
        self.assertEqual(OutboundSms.objects.count(), 1)  # cap reached: no new reply
        inbound.refresh_from_db()
        self.assertEqual(inbound.state, InboundSms.State.PROCESSED)
        self.assertEqual(inbound.reply_text, "")

    @mock.patch("phone_refresh.services.refresh_jobs.submit_refresh")
    def test_immediate_payload_finishes_inbound_now(self, submit):
        from phone_refresh.services.refresh_jobs import RefreshTicket

        submit.return_value = RefreshTicket(
            None, {"status": "busy", "message": {"title": "مشغول", "body": "حاول لاحقاً"}}
        )
        result = self._inbound()
        self.assertEqual(result.state, InboundSms.State.PROCESSED)
        self.assertEqual(result.reply_text, "مشغول\nحاول لاحقاً")
        self.assertIsNotNone(result.outbound_id)
        self.assertEqual(
            InboundSms.objects.get().state, InboundSms.State.PROCESSED
        )
        self.assertEqual(OutboundSms.objects.get().body, "مشغول\nحاول لاحقاً")

    @mock.patch("phone_refresh.services.refresh_jobs.submit_refresh")
    def test_immediate_payload_with_job_id_is_still_immediate(self, submit):
        from phone_refresh.services.refresh_jobs import RefreshTicket

        submit.return_value = RefreshTicket(
            "some-failed-job",
            {"status": "busy", "message": {"title": "مشغول", "body": "حاول لاحقاً"}},
        )
        result = self._inbound()
        self.assertEqual(result.state, InboundSms.State.PROCESSED)
        self.assertEqual(OutboundSms.objects.get().body, "مشغول\nحاول لاحقاً")

    @mock.patch("phone_refresh.services.refresh_jobs.submit_refresh", side_effect=RuntimeError("boom"))
    def test_submit_crash_does_not_leave_inbound_refreshing(self, submit):
        with self.assertLogs("sms_gateway.services.processing", level="ERROR"):
            result = self._inbound()
        self.assertEqual(result.state, InboundSms.State.PROCESSED)
        inbound = InboundSms.objects.get()
        self.assertEqual(inbound.state, InboundSms.State.PROCESSED)
        self.assertNotIn("boom", OutboundSms.objects.get().body)

    @mock.patch(PROVIDER_REFRESH)
    def test_enqueue_failure_finishes_inbound_with_busy_reply(self, refresh):
        from phone_refresh.tasks import run_refresh_job
        from providers.models import ProviderJob

        # The inbound view runs in autocommit, where on_commit fires at once
        # (TestCase's wrapping transaction would defer it).
        with mock.patch.object(run_refresh_job, "delay", side_effect=RuntimeError("redis down")), \
                mock.patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
            with self.assertLogs("phone_refresh.services.refresh_jobs", level="ERROR"):
                result = self._inbound()
        refresh.assert_not_called()
        self.assertEqual(result.state, InboundSms.State.PROCESSED)
        job = ProviderJob.objects.get()
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.result["status"], "busy")
        message = job.result["message"]
        expected = "\n".join(p for p in [message["title"].strip(), message["body"].strip()] if p)
        self.assertTrue(expected)
        self.assertEqual(OutboundSms.objects.get().body, expected)
        self.assertNotIn("redis down", expected)

    @mock.patch(PROVIDER_REFRESH)
    def test_enqueue_failure_after_commit_still_finishes_inbound(self, refresh):
        """Enqueue failing in a deferred on_commit (job ticket already returned)."""
        from phone_refresh.tasks import run_refresh_job

        with mock.patch.object(run_refresh_job, "delay", side_effect=RuntimeError("redis down")):
            with self.assertLogs("phone_refresh.services.refresh_jobs", level="ERROR"):
                with self.captureOnCommitCallbacks(execute=True):
                    result = self._inbound()
                    self.assertEqual(result.state, InboundSms.State.REFRESHING)
        refresh.assert_not_called()
        inbound = InboundSms.objects.get(pk=result.inbound_id)
        self.assertEqual(inbound.state, InboundSms.State.PROCESSED)
        self.assertEqual(OutboundSms.objects.count(), 1)

    @mock.patch(PROVIDER_REFRESH, side_effect=RuntimeError("secret provider boom"))
    def test_provider_crash_in_job_sends_generic_error_reply(self, refresh):
        from phone_refresh.tasks import error_payload

        with self.assertLogs("phone_refresh.tasks", level="ERROR"):
            with self.captureOnCommitCallbacks(execute=True):
                result = self._inbound()
        refresh.assert_called_once()
        inbound = InboundSms.objects.get(pk=result.inbound_id)
        self.assertEqual(inbound.state, InboundSms.State.PROCESSED)
        outbound = OutboundSms.objects.get()
        self.assertEqual(outbound.to_number, SENDER)
        self.assertTrue(outbound.body.strip())
        self.assertNotIn("secret provider boom", outbound.body)
        self.assertNotIn("boom", inbound.reply_text)
        message = error_payload()["message"]
        self.assertIn(message["body"].strip(), outbound.body)

    def test_reaper_completes_stuck_inbound_with_error_reply(self):
        from phone_refresh.tasks import reap_stuck_jobs
        from providers.models import ProviderJob

        with mock.patch(PROVIDER_REFRESH) as refresh:
            result = self._inbound()  # on_commit never runs: the job stays queued
        refresh.assert_not_called()
        job = ProviderJob.objects.get()
        ProviderJob.objects.filter(pk=job.pk).update(
            created_at=timezone.now() - timedelta(hours=1)
        )
        with self.assertLogs("phone_refresh.tasks", level="WARNING"):
            self.assertEqual(reap_stuck_jobs(), 1)
        inbound = InboundSms.objects.get(pk=result.inbound_id)
        self.assertEqual(inbound.state, InboundSms.State.PROCESSED)
        self.assertEqual(OutboundSms.objects.count(), 1)
        # A late worker finishing afterwards cannot send a second reply.
        from sms_gateway.services.processing import complete_inbound

        complete_inbound(result.inbound_id, "refreshed", "تم", "تم التحديث")
        self.assertEqual(OutboundSms.objects.count(), 1)

    @mock.patch("sms_gateway.services.processing.call_refresh_api", side_effect=_ok_outcome)
    def test_simulate_stays_synchronous(self, call_api):
        from sms_gateway.services.processing import process_inbound

        with mock.patch("phone_refresh.services.refresh_jobs.submit_refresh") as submit:
            result = process_inbound(from_number=SENDER, raw_text=REFRESHED_NUMBER, simulate=True)
        submit.assert_not_called()
        call_api.assert_called_once()
        self.assertEqual(result.state, InboundSms.State.PROCESSED)
        self.assertEqual(result.reply_text, "تم\nتم التحديث")
        self.assertEqual(InboundSms.objects.count(), 0)

    @mock.patch("sms_gateway.services.processing.call_refresh_api", side_effect=_ok_outcome)
    def test_external_api_url_stays_synchronous(self, call_api):
        s = SmsGatewaySettings.load()
        s.refresh_api_url = "https://gateway.example/refresh"
        s.save()
        cache.clear()
        with mock.patch("phone_refresh.services.refresh_jobs.submit_refresh") as submit:
            result = self._inbound()
        submit.assert_not_called()
        self.assertEqual(result.state, InboundSms.State.PROCESSED)
        self.assertEqual(OutboundSms.objects.count(), 1)

    @override_settings(REFRESH_ASYNC_ENABLED=False)
    @mock.patch("sms_gateway.services.processing.call_refresh_api", side_effect=_ok_outcome)
    def test_async_kill_switch_keeps_synchronous_flow(self, call_api):
        result = self._inbound()
        call_api.assert_called_once()
        self.assertEqual(result.state, InboundSms.State.PROCESSED)

    def test_refreshing_state_has_a_label(self):
        self.assertEqual(InboundSms.State.REFRESHING.value, "refreshing")
        self.assertIn(("refreshing", InboundSms.State.REFRESHING.label), InboundSms.State.choices)


class StuckInboundSweepTests(SmsBaseTest):
    def _refreshing(self, minutes_old, from_number=SENDER):
        inbound = InboundSms.objects.create(
            from_number=from_number,
            raw_text=REFRESHED_NUMBER,
            extracted_number=REFRESHED_NUMBER,
            state=InboundSms.State.REFRESHING,
        )
        InboundSms.objects.filter(pk=inbound.pk).update(
            received_at=timezone.now() - timedelta(minutes=minutes_old)
        )
        return inbound

    def test_sweep_fails_old_refreshing_inbound_with_generic_error_reply(self):
        from phone_refresh.tasks import error_payload
        from sms_gateway.tasks import sweep_stuck_inbound_sms

        old = self._refreshing(minutes_old=16)
        with self.assertLogs("sms_gateway.services.processing", level="WARNING"):
            self.assertEqual(sweep_stuck_inbound_sms(), 1)
        old.refresh_from_db()
        self.assertEqual(old.state, InboundSms.State.PROCESSED)
        outbound = OutboundSms.objects.get()
        self.assertEqual(outbound.to_number, SENDER)
        message = error_payload()["message"]
        self.assertIn(message["body"].strip(), outbound.body)
        # Second run finds nothing and sends nothing more.
        self.assertEqual(sweep_stuck_inbound_sms(), 0)
        self.assertEqual(OutboundSms.objects.count(), 1)

    def test_sweep_leaves_recent_and_other_states_alone(self):
        from sms_gateway.tasks import sweep_stuck_inbound_sms

        recent = self._refreshing(minutes_old=5, from_number="0595108209")
        other = self._refreshing(minutes_old=60, from_number="0595108210")
        InboundSms.objects.filter(pk=other.pk).update(state=InboundSms.State.PROCESSED)
        self.assertEqual(sweep_stuck_inbound_sms(), 0)
        recent.refresh_from_db()
        self.assertEqual(recent.state, InboundSms.State.REFRESHING)
        self.assertEqual(OutboundSms.objects.count(), 0)

    def test_sweep_is_scheduled_every_five_minutes(self):
        from django.conf import settings

        entry = settings.CELERY_BEAT_SCHEDULE["sms-gateway-sweep-stuck-inbound"]
        self.assertEqual(entry["task"], "sms_gateway.tasks.sweep_stuck_inbound_sms")
        self.assertEqual(entry["schedule"], 300.0)

    def test_sweep_task_is_registered_with_celery(self):
        from config.celery import app

        app.loader.import_default_modules()
        self.assertIn("sms_gateway.tasks.sweep_stuck_inbound_sms", app.tasks)
