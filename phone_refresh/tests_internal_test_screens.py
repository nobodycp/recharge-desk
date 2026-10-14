"""Admin internal-test screens run through the job queue and keep raw diagnostics."""
from __future__ import annotations

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import UserProfile
from phone_refresh.models import (
    MatchType,
    PhoneProvider,
    ProviderResponseRule,
    RefreshSource,
    RefreshStatus,
    SystemSettings,
)
from phone_refresh.services import refresh_jobs
from phone_refresh.services.refresh_jobs import job_payload, submit_refresh
from phone_refresh.services.refresh_service import (
    INTERNAL_RESULT_KEYS,
    RefreshResult,
    internal_result_extras,
)
from providers.contracts import RawResponse
from providers.models import ProviderJob
from providers.redis_conn import get_redis

User = get_user_model()

PHONE = "0529000000"
PROVIDER_REFRESH = "providers.service.refresh"
EXCERPT_CAP = 500
SECRET_BODY = 'ok {"jwtToken": "abc.def.ghi"} password=hunter2 ' + "x" * 600


def _raw(text: str = "", data=None) -> RawResponse:
    return RawResponse(text=text, json=data, status_code=200)


class _ScreenTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user("admin_screen", password="x")
        UserProfile.objects.update_or_create(
            user=cls.admin,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )

    def setUp(self):
        get_redis().flushall()
        s = SystemSettings.get()
        s.db_precheck_enabled = False
        s.default_provider = "sky"
        s.show_last_refresh = False
        s.save()
        self.client = Client()
        self.client.force_login(self.admin)

    def _post(self, name, data):
        return self.client.post(reverse(name), data)


class RawExcerptStorageTests(_ScreenTestCase):
    def _submit(self, **kwargs):
        with self.captureOnCommitCallbacks(execute=True):
            return submit_refresh(PHONE, ip=None, source=RefreshSource.WEB, **kwargs)

    @patch(PROVIDER_REFRESH, return_value=_raw(text=SECRET_BODY))
    def test_internal_test_job_stores_scrubbed_truncated_excerpt(self, refresh):
        ticket = self._submit(
            internal_test=True, forced_provider="sky", bypass_provider_off=True
        )
        result = ProviderJob.objects.get(pk=ticket.job_id).result
        excerpt = result["raw_excerpt"]
        self.assertLessEqual(len(excerpt), 500)
        self.assertNotIn("abc.def.ghi", excerpt)
        self.assertNotIn("hunter2", excerpt)
        self.assertIn("***", excerpt)

    @patch(PROVIDER_REFRESH)
    def test_secret_straddling_the_cap_never_reaches_the_excerpt(self, refresh):
        secret = "SUPERSECRETJWTVALUE1234567890"
        prefix = "a" * (EXCERPT_CAP - 20)
        body = prefix + '"jwtToken": "' + secret + '" tail'
        # The key starts before char 500 and the value crosses it.
        self.assertLess(body.index("jwtToken"), EXCERPT_CAP)
        self.assertGreater(body.index(secret) + len(secret), EXCERPT_CAP)
        refresh.return_value = _raw(text=body)
        ticket = self._submit(
            internal_test=True, forced_provider="sky", bypass_provider_off=True
        )
        excerpt = ProviderJob.objects.get(pk=ticket.job_id).result["raw_excerpt"]
        self.assertLessEqual(len(excerpt), EXCERPT_CAP)
        self.assertNotIn(secret, excerpt)
        self.assertNotIn(secret[:8], excerpt)

    def test_extras_scrub_the_full_body_before_capping(self):
        secret = "SUPERSECRETJWTVALUE1234567890"
        body = "a" * (EXCERPT_CAP - 20) + '"jwtToken": "' + secret + '" tail'
        result = RefreshResult(
            status=None,
            provider="sky",
            message_title="",
            message_body="",
            matched_rule_id=None,
            raw_status_code=200,
            raw_excerpt=body[:EXCERPT_CAP],
            raw_body_full=body,
        )
        excerpt = internal_result_extras(result)["raw_excerpt"]
        self.assertLessEqual(len(excerpt), EXCERPT_CAP)
        self.assertNotIn(secret[:8], excerpt)
        self.assertIn("***", excerpt)

    @patch(PROVIDER_REFRESH, return_value=_raw(text=SECRET_BODY))
    def test_stored_internal_keys_are_exactly_the_declared_ones(self, refresh):
        plain = self._submit()
        internal = self._submit(
            internal_test=True, forced_provider="sky", bypass_provider_off=True
        )
        plain_keys = set(ProviderJob.objects.get(pk=plain.job_id).result)
        internal_keys = set(ProviderJob.objects.get(pk=internal.job_id).result)
        # A new internal-only key must be added to INTERNAL_RESULT_KEYS (and
        # so stripped by job_payload) or this fails.
        self.assertEqual(internal_keys - set(INTERNAL_RESULT_KEYS), plain_keys)
        self.assertEqual(internal_keys - plain_keys, set(INTERNAL_RESULT_KEYS))

    @patch(PROVIDER_REFRESH, return_value=_raw(data={"reason": "success"}))
    def test_job_payload_only_serves_refresh_jobs(self, refresh):
        ticket = self._submit()
        self.assertEqual(job_payload(ticket.job_id)["status"], "refreshed")
        for kind in (ProviderJob.KIND_CHARGE, ProviderJob.KIND_CHECK_CHARGE):
            ProviderJob.objects.filter(pk=ticket.job_id).update(kind=kind)
            self.assertEqual(
                job_payload(ticket.job_id),
                job_payload("00000000-0000-0000-0000-000000000000"),
            )
            response = Client().get(
                reverse("phone_refresh:public_status", args=[ticket.job_id])
            )
            self.assertEqual(response.json()["status"], "error")
            self.assertNotIn("refreshed", response.content.decode())

    @patch(PROVIDER_REFRESH, return_value=_raw(text=SECRET_BODY))
    def test_plain_job_never_stores_excerpt(self, refresh):
        ticket = self._submit()
        result = ProviderJob.objects.get(pk=ticket.job_id).result
        self.assertNotIn("raw_excerpt", result)
        self.assertNotIn("diagnostics", result)

    @patch(PROVIDER_REFRESH, return_value=_raw(text=SECRET_BODY))
    def test_job_payload_strips_internal_keys(self, refresh):
        ticket = self._submit(
            internal_test=True, forced_provider="sky", bypass_provider_off=True
        )
        stored = ProviderJob.objects.get(pk=ticket.job_id).result
        self.assertIn("raw_excerpt", stored)
        payload = job_payload(ticket.job_id)
        self.assertNotIn("raw_excerpt", payload)
        self.assertNotIn("diagnostics", payload)
        self.assertIn("status", payload)
        self.assertIn("message", payload)

    @patch(PROVIDER_REFRESH, side_effect=RuntimeError("boom password=hunter2"))
    def test_crashed_internal_test_job_keeps_scrubbed_error_diagnostic(self, refresh):
        with self.assertLogs("phone_refresh.tasks", level="ERROR"):
            ticket = self._submit(
                internal_test=True, forced_provider="sky", bypass_provider_off=True
            )
        stored = ProviderJob.objects.get(pk=ticket.job_id).result
        self.assertEqual(stored["status"], "error")
        self.assertNotIn("hunter2", json.dumps(stored))
        self.assertIn("boom", stored["diagnostics"]["error"])

    @patch(PROVIDER_REFRESH, return_value=_raw(text=SECRET_BODY))
    def test_public_status_endpoint_does_not_expose_raw_excerpt(self, refresh):
        ticket = self._submit(
            internal_test=True, forced_provider="sky", bypass_provider_off=True
        )
        self.assertIn("raw_excerpt", ProviderJob.objects.get(pk=ticket.job_id).result)
        response = Client().get(
            reverse("phone_refresh:public_status", args=[ticket.job_id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("raw_excerpt", response.json())
        self.assertNotIn("raw_excerpt", response.content.decode())
        self.assertNotIn("diagnostics", response.json())


class InternalTestViewTests(_ScreenTestCase):
    @patch(PROVIDER_REFRESH, return_value=_raw(text=SECRET_BODY))
    def test_general_internal_test_renders_job_result_and_raw_excerpt(self, refresh):
        response = self._post("phone_refresh:settings_internal_test", {"phone": PHONE})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["phone"], PHONE)
        self.assertEqual(data["provider"], "sky")
        self.assertEqual(data["status"], "error")  # no rule matches this body
        self.assertTrue(data["status_label"])
        self.assertEqual(data["raw_status_code"], 200)
        self.assertLessEqual(len(data["raw_excerpt"]), 500)
        self.assertNotIn("hunter2", data["raw_excerpt"])
        job = ProviderJob.objects.get()
        self.assertEqual(job.source, RefreshSource.INTERNAL_TEST)
        self.assertTrue(job.payload["internal_test"])
        self.assertEqual(job.status, "done")

    def test_general_internal_test_rejects_invalid_phone(self):
        response = self._post("phone_refresh:settings_internal_test", {"phone": "12"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
        self.assertEqual(ProviderJob.objects.count(), 0)

    @patch(
        "phone_refresh.services.refresh_jobs.wait_for_job",
        return_value={"status": "processing", "job": "still-running"},
    )
    def test_general_internal_test_still_processing_shows_final_message(self, wait):
        response = self._post("phone_refresh:settings_internal_test", {"phone": PHONE})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["status"], "processing")
        self.assertTrue(data["message"]["body"])
        self.assertEqual(data["raw_excerpt"], "")
        self.assertEqual(wait.call_args[0][1], 45)

    @override_settings(REFRESH_ASYNC_ENABLED=False)
    @patch(PROVIDER_REFRESH, return_value=_raw(text=SECRET_BODY))
    def test_general_internal_test_with_async_disabled_keeps_diagnostics(self, refresh):
        response = self._post("phone_refresh:settings_internal_test", {"phone": PHONE})
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["raw_status_code"], 200)
        self.assertTrue(data["raw_excerpt"])
        self.assertNotIn("hunter2", data["raw_excerpt"])
        self.assertEqual(ProviderJob.objects.count(), 0)

    def test_internal_test_requires_management(self):
        client = Client()
        plain = User.objects.create_user("plain_screen", password="x")
        UserProfile.objects.update_or_create(
            user=plain,
            defaults={"role": UserProfile.Role.EMPLOYEE, "is_active_profile": True},
        )
        client.force_login(plain)
        response = client.post(
            reverse("phone_refresh:settings_internal_test"), {"phone": PHONE}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ProviderJob.objects.count(), 0)


class ProviderTestViewTests(_ScreenTestCase):
    @patch(PROVIDER_REFRESH, return_value=_raw(text=SECRET_BODY))
    def test_provider_test_forces_provider_and_bypasses_off_toggle(self, refresh):
        from phone_refresh.models import ProviderConfig

        ProviderConfig.objects.filter(provider="layan").update(is_enabled=False)
        response = self._post(
            "phone_refresh:providers_test", {"phone": PHONE, "provider": "layan"}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["provider"], "layan")
        self.assertEqual(data["provider_label"], dict(PhoneProvider.choices)["layan"])
        self.assertEqual(data["raw_status_code"], 200)
        self.assertEqual(data["raw_body"], data["raw_excerpt"])
        self.assertNotIn("hunter2", data["raw_body"])
        self.assertNotIn("abc.def.ghi", data["raw_body"])
        self.assertGreaterEqual(data["duration_ms"], 0)
        job = ProviderJob.objects.get()
        self.assertEqual(job.provider_key, "layan")
        self.assertEqual(job.payload["forced_provider"], "layan")
        self.assertTrue(job.payload["bypass_provider_off"])
        refresh.assert_called_once()
        self.assertEqual(refresh.call_args[0][0], "layan")

    @patch(PROVIDER_REFRESH, return_value=_raw(data={"reason": "success"}))
    def test_provider_test_reports_matched_rule(self, refresh):
        ProviderResponseRule.objects.filter(provider="sky").delete()
        rule = ProviderResponseRule.objects.create(
            provider="sky",
            order=1,
            match_type=MatchType.JSON_PATH,
            pattern="$.reason",
            expected_value="success",
            target_status=RefreshStatus.objects.get(code="refreshed"),
        )
        response = self._post(
            "phone_refresh:providers_test", {"phone": PHONE, "provider": "sky"}
        )
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["status"], "refreshed")
        self.assertEqual(
            data["matched_rule"],
            {
                "id": rule.pk,
                "match_type": rule.get_match_type_display(),
                "pattern": "$.reason",
                "expected_value": "success",
                "order": 1,
                "target_status": rule.target_status.label,
            },
        )

    @patch(
        "phone_refresh.services.refresh_jobs.wait_for_job",
        return_value={"status": "processing", "job": "still-running"},
    )
    def test_provider_test_still_processing_shows_final_message(self, wait):
        response = self._post(
            "phone_refresh:providers_test", {"phone": PHONE, "provider": "sky"}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "processing")
        self.assertTrue(data["message"]["body"])
        self.assertEqual(data["raw_body"], "")
        self.assertEqual(data["raw_body_length"], 0)
        self.assertEqual(wait.call_args[0][1], 45)

    def test_provider_test_rejects_bad_input(self):
        response = self._post(
            "phone_refresh:providers_test", {"phone": "1", "provider": "sky"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ProviderJob.objects.count(), 0)


class RunInternalTestHelperTests(_ScreenTestCase):
    @patch(PROVIDER_REFRESH, return_value=_raw(text="hello"))
    def test_outcome_exposes_excerpt_and_diagnostics(self, refresh):
        outcome = refresh_jobs.run_internal_test(
            PHONE, forced_provider="sky", bypass_provider_off=True
        )
        self.assertEqual(outcome.raw_excerpt, "hello")
        self.assertEqual(outcome.diagnostics["provider"], "sky")
        self.assertEqual(outcome.diagnostics["raw_status_code"], 200)
        self.assertNotIn("raw_excerpt", outcome.payload)

    def test_precheck_hit_has_no_job_and_no_excerpt(self):
        outcome = refresh_jobs.run_internal_test("123")
        self.assertEqual(outcome.payload["status"], "not_found")
        self.assertEqual(outcome.raw_excerpt, "")
        self.assertEqual(ProviderJob.objects.count(), 0)
