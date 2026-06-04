import hashlib
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
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
