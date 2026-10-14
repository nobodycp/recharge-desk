"""Public refresh API auth gate tests."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from unittest.mock import patch

from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone, translation

from kombu.exceptions import OperationalError

from phone_refresh.models import (
    ApiSettings,
    ApiToken,
    RefreshSource,
    RefreshStatus,
    SiteSettings,
    SystemSettings,
)
from phone_refresh.services.refresh_jobs import RefreshTicket
from phone_refresh.services.refresh_service import (
    RefreshResult,
    result_payload,
    status_payload,
)
from phone_refresh.tasks import run_refresh_job
from phone_refresh.views.public import public_refresh_api
from providers.contracts import RawResponse
from providers.models import ProviderJob
from providers.redis_conn import get_redis


def _ticket(**result_kwargs) -> RefreshTicket:
    """A ready-payload ticket (no job), as ``submit_refresh`` returns for precheck hits."""
    return RefreshTicket(None, result_payload(RefreshResult(**result_kwargs)))


class PublicRefreshApiTokenGateTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        ApiSettings.objects.update_or_create(
            pk=1,
            defaults={
                "require_token": True,
                "allow_anonymous_test_page": False,
                "rate_limit_per_minute": 0,
                "rate_limit_per_hour": 0,
                "allowed_origins": "",
            },
        )
        self.error_status = RefreshStatus.objects.get(code="error")
        get_redis().flushall()

    def _post(self, payload: dict, *, headers: dict | None = None):
        request = self.factory.post(
            reverse("phone_refresh:public_api"),
            data=json.dumps(payload),
            content_type="application/json",
            **(headers or {}),
        )
        return public_refresh_api(request)

    @patch("phone_refresh.views.public.submit_refresh")
    def test_web_client_with_valid_bearer_token(self, refresh_mock):
        raw = "test-public-page-token-value"
        token = ApiToken.objects.create(
            name="صفحة التحديث العامة",
            token_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            prefix=raw[:8],
        )
        refresh_mock.return_value = _ticket(
            status=self.error_status,
            provider=None,
            message_title="",
            message_body="",
            matched_rule_id=None,
            raw_status_code=None,
            raw_excerpt="",
        )
        response = self._post(
            {"phone_number": "0501234567", "client": RefreshSource.WEB},
            headers={"HTTP_AUTHORIZATION": f"Bearer {raw}"},
        )
        self.assertEqual(response.status_code, 200)
        refresh_mock.assert_called_once()
        token.refresh_from_db()
        self.assertIsNotNone(token.last_used_at)

    @patch("phone_refresh.views.public.submit_refresh")
    def test_web_client_bypasses_token_when_anonymous_page_allowed(self, refresh_mock):
        ApiSettings.objects.filter(pk=1).update(allow_anonymous_test_page=True)
        refresh_mock.return_value = _ticket(
            status=self.error_status,
            provider=None,
            message_title="",
            message_body="",
            matched_rule_id=None,
            raw_status_code=None,
            raw_excerpt="",
        )
        response = self._post(
            {"phone_number": "0501234567", "client": RefreshSource.WEB},
        )
        self.assertEqual(response.status_code, 200)
        refresh_mock.assert_called_once()

    def test_api_client_requires_token(self):
        response = self._post({"phone_number": "0501234567"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(json.loads(response.content), {"error": "missing_token"})

    def test_web_client_requires_token_when_anonymous_page_disabled(self):
        response = self._post(
            {"phone_number": "0501234567", "client": RefreshSource.WEB},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(json.loads(response.content), {"error": "missing_token"})


@override_settings(TRUST_FORWARDED_FOR=True)
class PublicRefreshApiClientIpTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        ApiSettings.objects.update_or_create(
            pk=1,
            defaults={
                "require_token": False,
                "allow_anonymous_test_page": True,
                "rate_limit_per_minute": 0,
                "rate_limit_per_hour": 0,
                "allowed_origins": "",
            },
        )
        self.error_status = RefreshStatus.objects.get(code="error")
        get_redis().flushall()

    @patch("phone_refresh.views.public.submit_refresh")
    def test_uses_cf_connecting_ip_for_refresh_log(self, refresh_mock):
        refresh_mock.return_value = _ticket(
            status=self.error_status,
            provider=None,
            message_title="",
            message_body="",
            matched_rule_id=None,
            raw_status_code=None,
            raw_excerpt="",
        )
        request = self.factory.post(
            reverse("phone_refresh:public_api"),
            data=json.dumps({"phone_number": "0501234567", "client": RefreshSource.WEB}),
            content_type="application/json",
            REMOTE_ADDR="203.0.113.10",
            HTTP_CF_CONNECTING_IP="198.51.100.20",
            HTTP_X_FORWARDED_FOR="198.51.100.30, 203.0.113.1",
        )
        response = public_refresh_api(request)
        self.assertEqual(response.status_code, 200)
        refresh_mock.assert_called_once()
        self.assertEqual(refresh_mock.call_args.kwargs["ip"], "198.51.100.20")

    @patch("phone_refresh.views.public.submit_refresh")
    def test_rate_limit_uses_same_client_ip(self, refresh_mock):
        ApiSettings.objects.filter(pk=1).update(rate_limit_per_minute=1)
        refresh_mock.return_value = _ticket(
            status=self.error_status,
            provider=None,
            message_title="",
            message_body="",
            matched_rule_id=None,
            raw_status_code=None,
            raw_excerpt="",
        )
        headers = {
            "REMOTE_ADDR": "203.0.113.10",
            "HTTP_CF_CONNECTING_IP": "198.51.100.20",
        }
        payload = json.dumps({"phone_number": "0501234567", "client": RefreshSource.WEB})

        first = self.factory.post(
            reverse("phone_refresh:public_api"),
            data=payload,
            content_type="application/json",
            **headers,
        )
        second = self.factory.post(
            reverse("phone_refresh:public_api"),
            data=payload,
            content_type="application/json",
            **headers,
        )
        self.assertEqual(public_refresh_api(first).status_code, 200)
        self.assertEqual(public_refresh_api(second).status_code, 429)


PHONE = "0529000000"
PROVIDER_REFRESH = "providers.service.refresh"


def _raw_success() -> RawResponse:
    return RawResponse(text="", json={"reason": "success"}, status_code=200)


class PublicRefreshAsyncApiTests(TestCase):
    """End-to-end through the real ``submit_refresh`` / ``ProviderJob`` pipeline."""

    def setUp(self):
        get_redis().flushall()
        ApiSettings.objects.update_or_create(
            pk=1,
            defaults={
                "require_token": False,
                "allow_anonymous_test_page": True,
                "rate_limit_per_minute": 0,
                "rate_limit_per_hour": 0,
                "allowed_origins": "",
            },
        )
        s = SystemSettings.get()
        s.db_precheck_enabled = False
        s.default_provider = "sky"
        s.show_last_refresh = False
        s.save()
        self.client = Client()
        self.url = reverse("phone_refresh:public_api")

    def _post(self, phone=PHONE, **extra):
        body = {"phone_number": phone, "client": RefreshSource.WEB, **extra}
        return self.client.post(
            self.url, data=json.dumps(body), content_type="application/json"
        )

    def _status_url(self, job_id):
        return reverse("phone_refresh:public_status", args=[job_id])

    def test_async_request_returns_202_with_job(self):
        response = self._post(**{"async": True})
        self.assertEqual(response.status_code, 202)
        job = ProviderJob.objects.get()
        self.assertEqual(
            response.json(), {"status": "processing", "job": str(job.id)}
        )

    @patch(PROVIDER_REFRESH, return_value=_raw_success())
    def test_status_endpoint_returns_final_payload(self, refresh):
        job_id = self._post(**{"async": True}).json()["job"]

        pending = self.client.get(self._status_url(job_id))
        self.assertEqual(pending.status_code, 200)
        self.assertEqual(pending.json()["status"], "processing")

        run_refresh_job(job_id)

        response = self.client.get(self._status_url(job_id))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "refreshed")
        for internal in ("provider_key", "ip", "raw", "raw_body", "payload"):
            self.assertNotIn(internal, payload)

    def test_status_endpoint_returns_only_job_payload_output(self):
        stub = {"status": "wait", "message": {"title": "t", "body": "b"}}
        with patch("phone_refresh.views.public.job_payload", return_value=stub) as jp:
            response = self.client.get(self._status_url("5f0d7f0e-6a0b-4f3a-9a53-0c4c1f1f8f11"))
        self.assertEqual(response.json(), stub)
        jp.assert_called_once_with("5f0d7f0e-6a0b-4f3a-9a53-0c4c1f1f8f11")

    def test_unknown_job_id_returns_generic_error_payload_with_200(self):
        response = self.client.get(
            self._status_url("5f0d7f0e-6a0b-4f3a-9a53-0c4c1f1f8f11")
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "error")

    def test_status_endpoint_is_get_only(self):
        response = self.client.post(
            self._status_url("5f0d7f0e-6a0b-4f3a-9a53-0c4c1f1f8f11")
        )
        self.assertEqual(response.status_code, 405)

    def test_status_rate_limit_is_per_job_not_per_ip(self):
        busy_job = "5f0d7f0e-6a0b-4f3a-9a53-0c4c1f1f8f11"
        other_job = "9b2e7c1d-3f4a-4b5c-8d6e-7f8091a2b3c4"
        get_redis().set(f"ratelimit:refresh-status:127.0.0.1:{busy_job}", 60, ex=60)
        self.assertEqual(self.client.get(self._status_url(busy_job)).status_code, 429)
        # A second customer behind the same IP polling another job is fine.
        self.assertEqual(self.client.get(self._status_url(other_job)).status_code, 200)

    def test_status_job_bucket_counts_each_poll(self):
        job = "5f0d7f0e-6a0b-4f3a-9a53-0c4c1f1f8f11"
        for _ in range(60):
            self.assertEqual(self.client.get(self._status_url(job)).status_code, 200)
        self.assertEqual(self.client.get(self._status_url(job)).status_code, 429)

    def test_status_ip_wide_ceiling_blocks_enumeration(self):
        get_redis().set("ratelimit:refresh-status-ip:127.0.0.1", 600, ex=60)
        response = self.client.get(
            self._status_url("5f0d7f0e-6a0b-4f3a-9a53-0c4c1f1f8f11")
        )
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["status"], "error")

    def test_status_response_is_never_cached(self):
        response = self.client.get(
            self._status_url("5f0d7f0e-6a0b-4f3a-9a53-0c4c1f1f8f11")
        )
        self.assertIn("no-cache", response["Cache-Control"])
        self.assertIn("no-store", response["Cache-Control"])

    def test_status_for_job_older_than_wait_setting_is_final(self):
        job_id = self._post(**{"async": True}).json()["job"]
        wait = SystemSettings.get().refresh_wait_seconds
        ProviderJob.objects.filter(pk=job_id).update(
            created_at=timezone.now() - dt.timedelta(seconds=wait + 5)
        )
        payload = self.client.get(self._status_url(job_id)).json()
        self.assertEqual(payload["status"], "processing")
        self.assertIs(payload["final"], True)
        self.assertNotIn("job", payload)
        self.assertTrue(payload["message"]["body"])

    @patch(PROVIDER_REFRESH, return_value=_raw_success())
    def test_sync_client_gets_final_payload(self, refresh):
        response = self._post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "refreshed")
        self.assertEqual(ProviderJob.objects.get().status, "done")

    def test_sync_non_final_processing_becomes_final_processing_message(self):
        stuck = {"status": "processing", "job": "whatever"}
        with patch("phone_refresh.views.public.wait_for_job", return_value=stuck):
            response = self._post()
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload, status_payload("processing", final=True))
        self.assertNotIn("job", payload)

    def test_sync_final_processing_payload_passes_through(self):
        final = status_payload("processing", final=True)
        with patch("phone_refresh.views.public.wait_for_job", return_value=final):
            response = self._post()
        self.assertEqual(response.json(), final)

    def test_sync_precheck_hit_returns_payload_without_job(self):
        response = self._post(phone="123")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "not_found")
        self.assertEqual(ProviderJob.objects.count(), 0)

    def test_async_precheck_hit_returns_200_payload_without_job(self):
        response = self._post(phone="123", **{"async": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "not_found")
        self.assertEqual(ProviderJob.objects.count(), 0)

    @patch("phone_refresh.services.refresh_jobs.transaction.on_commit", side_effect=lambda fn: fn())
    @patch(
        "phone_refresh.services.refresh_jobs.run_refresh_job.delay",
        side_effect=OperationalError("broker down"),
    )
    def test_enqueue_failure_returns_busy(self, delay, on_commit):
        for extra in ({"async": True}, {}):
            with self.assertLogs("phone_refresh.services.refresh_jobs", level="ERROR"):
                response = self._post(**extra)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "busy")
        self.assertEqual(
            set(ProviderJob.objects.values_list("status", flat=True)), {"failed"}
        )

    def test_inflight_job_reused(self):
        first = self._post(**{"async": True})
        second = self._post(**{"async": True})
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first.json()["job"], second.json()["job"])
        self.assertEqual(ProviderJob.objects.count(), 1)

    def test_rate_limit_shared_via_redis(self):
        ApiSettings.objects.filter(pk=1).update(rate_limit_per_minute=1)
        self.assertEqual(self._post(**{"async": True}).status_code, 202)
        limited = self._post(**{"async": True})
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.json()["status"], "error")
        # The counter lives in Redis, not in process memory.
        self.assertEqual(int(get_redis().get("ratelimit:refresh-min:127.0.0.1")), 2)

    def test_hourly_limit_applies(self):
        ApiSettings.objects.filter(pk=1).update(rate_limit_per_hour=1)
        self.assertEqual(self._post(phone="123").status_code, 200)
        self.assertEqual(self._post(phone="123").status_code, 429)
        self.assertEqual(int(get_redis().get("ratelimit:refresh-hour:127.0.0.1")), 2)
        self.assertIsNone(get_redis().get("ratelimit:refresh-min:127.0.0.1"))

    def test_async_flag_in_form_post_is_honoured(self):
        response = self.client.post(
            self.url, {"phone_number": PHONE, "client": "web", "async": "1"}
        )
        self.assertEqual(response.status_code, 202)

    def test_public_page_exposes_status_url_and_wait_seconds(self):
        SystemSettings.objects.filter(pk=SystemSettings.get().pk).update(
            refresh_wait_seconds=75
        )
        page = self.client.get(reverse("phone_refresh:public"))
        self.assertContains(page, 'name="refresh-wait-seconds" content="75"')
        with translation.override("ar"):  # the page's default public language
            status_template = reverse(
                "phone_refresh:public_status",
                args=["00000000-0000-0000-0000-000000000000"],
            )
        self.assertContains(
            page, f'name="refresh-status-url" content="{status_template}"'
        )


class PublicRefreshPageTokenInjectionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.site_settings = SiteSettings.get_solo()

    def test_page_includes_token_meta_when_configured(self):
        raw = "embedded-public-token"
        token = ApiToken.objects.create(
            name="صفحة التحديث العامة",
            token_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            prefix=raw[:8],
        )
        self.site_settings.public_page_token = token
        self.site_settings.public_page_token_raw = raw
        self.site_settings.save()

        response = self.client.get(reverse("phone_refresh:public"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'<meta name="refresh-api-token" content="{raw}" />',
            html=True,
        )

    def test_page_omits_token_meta_when_not_configured(self):
        self.site_settings.public_page_token = None
        self.site_settings.public_page_token_raw = ""
        self.site_settings.save()

        response = self.client.get(reverse("phone_refresh:public"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="refresh-api-token"')

    def test_page_omits_token_meta_when_token_revoked(self):
        from django.utils import timezone

        raw = "revoked-public-token"
        token = ApiToken.objects.create(
            name="صفحة التحديث العامة",
            token_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            prefix=raw[:8],
        )
        token.revoked_at = timezone.now()
        token.save(update_fields=["revoked_at"])
        self.site_settings.public_page_token = token
        self.site_settings.public_page_token_raw = raw
        self.site_settings.save()

        response = self.client.get(reverse("phone_refresh:public"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="refresh-api-token"')
