"""``/api/v1/refresh/`` endpoints: bearer auth, scopes, ownership, rate limit."""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from phone_refresh.forms import ApiTokenForm
from phone_refresh.models import (
    API_SCOPES,
    ApiSettings,
    ApiToken,
    RefreshSource,
    SystemSettings,
)
from phone_refresh.services.refresh_jobs import submit_refresh
from providers.contracts import RawResponse
from providers.models import ProviderJob
from providers.redis_conn import get_redis

PHONE = "0529000000"
URL = "/api/v1/refresh/"
PROVIDER_REFRESH = "providers.service.refresh"


def _raw() -> RawResponse:
    return RawResponse(text="", json={"reason": "success"}, status_code=200)


def make_token(name="store", scopes="refresh") -> tuple[ApiToken, str]:
    raw = f"raw-{uuid.uuid4().hex}"
    token = ApiToken.objects.create(
        name=name,
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        prefix=raw[:8],
        scopes=scopes,
    )
    return token, raw


class _ApiTestCase(TestCase):
    def setUp(self):
        get_redis().flushall()
        s = SystemSettings.get()
        s.db_precheck_enabled = False
        s.default_provider = "sky"
        s.show_last_refresh = False
        s.save()
        self.token, self.raw = make_token()
        self.client = Client(enforce_csrf_checks=True)
        # Never reach a real provider, whatever a test does with the jobs.
        patcher = patch(PROVIDER_REFRESH, return_value=_raw())
        self.provider_refresh = patcher.start()
        self.addCleanup(patcher.stop)

    def auth(self, raw=None):
        return {"HTTP_AUTHORIZATION": f"Bearer {raw or self.raw}"}

    def post(self, body=None, raw=None, run_jobs=True, **extra):
        if body is None:
            body = {"phone": PHONE}
        data = body if isinstance(body, str) else json.dumps(body)
        headers = {} if raw == "" else self.auth(raw)
        with self.captureOnCommitCallbacks(execute=run_jobs):
            return self.client.post(
                URL, data=data, content_type="application/json", **headers, **extra
            )

    def get(self, job_id, raw=None):
        return self.client.get(f"{URL}{job_id}/", **self.auth(raw))


class AuthTests(_ApiTestCase):
    def test_missing_token_401(self):
        resp = self.post(raw="")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json(), {"error": "unauthorized"})
        self.assertEqual(resp["WWW-Authenticate"], "Bearer")

    def test_unknown_and_revoked_token_401(self):
        self.assertEqual(self.post(raw="nope").status_code, 401)
        self.token.revoked_at = timezone.now()
        self.token.save()
        resp = self.post()
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp["WWW-Authenticate"], "Bearer")

    def test_status_endpoint_requires_token(self):
        resp = self.client.get(f"{URL}{uuid.uuid4()}/")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json(), {"error": "unauthorized"})

    def test_anonymous_mode_does_not_open_the_api(self):
        ApiSettings.objects.update_or_create(
            pk=1,
            defaults={"require_token": False, "allow_anonymous_test_page": True},
        )
        self.assertEqual(self.post(raw="").status_code, 401)

    def test_token_without_scope_403(self):
        _, raw = make_token("noscope", scopes="charge")
        resp = self.post(raw=raw)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json(), {"error": "insufficient_scope"})
        self.assertEqual(ProviderJob.objects.count(), 0)
        self.assertEqual(self.get(uuid.uuid4(), raw=raw).status_code, 403)

    def test_scope_matching_trims_and_lowercases(self):
        token, raw = make_token("spacey", scopes=" Charge , REFRESH ")
        self.assertTrue(token.has_scope("refresh"))
        self.assertTrue(token.has_scope(" Charge"))
        self.assertFalse(token.has_scope("admin"))
        self.assertFalse(token.has_scope(""))
        self.assertNotEqual(self.post(raw=raw).status_code, 403)

    def test_successful_auth_bumps_last_used_at(self):
        self.assertIsNone(self.token.last_used_at)
        self.post({"phone": "123"})
        self.token.refresh_from_db()
        self.assertIsNotNone(self.token.last_used_at)

    def test_default_scope_is_refresh(self):
        token = ApiToken.objects.create(name="d", token_hash="h" * 64, prefix="hhhhhhhh")
        self.assertEqual(token.scopes, "refresh")
        self.assertTrue(token.has_scope("refresh"))

    def test_csrf_not_required_for_bearer_api(self):
        resp = self.post({"phone": "123"})  # client enforces CSRF checks
        self.assertEqual(resp.status_code, 200)

    def test_method_enforcement(self):
        self.assertEqual(self.client.get(URL, **self.auth()).status_code, 405)
        resp = self.client.post(f"{URL}{uuid.uuid4()}/", **self.auth())
        self.assertEqual(resp.status_code, 405)

    def test_responses_are_not_cacheable(self):
        self.assertIn("no-cache", self.post({"phone": "123"})["Cache-Control"])
        self.assertIn("no-cache", self.get(uuid.uuid4())["Cache-Control"])


class RefreshFlowTests(_ApiTestCase):
    def test_invalid_json_400(self):
        for body in ("{not json", "[1, 2]", '"x"'):
            resp = self.post(body)
            self.assertEqual(resp.status_code, 400, body)
            self.assertEqual(resp.json(), {"error": "invalid_json"})

    def test_phone_required_400(self):
        for body in ({}, {"phone": ""}, {"phone": "   "}, {"phone": None}, {"phone": 5}):
            resp = self.post(body)
            self.assertEqual(resp.status_code, 400, body)
            self.assertEqual(resp.json(), {"error": "phone_required"})

    def test_invalid_phone_is_a_business_outcome_200(self):
        resp = self.post({"phone": "123"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "not_found")
        self.assertEqual(ProviderJob.objects.count(), 0)

    def test_refresh_returns_job_then_status(self):
        resp = self.post()
        self.assertEqual(resp.status_code, 202)
        body = resp.json()
        self.assertEqual(body["status"], "processing")
        job = ProviderJob.objects.get()
        self.assertEqual(body["job"], str(job.id))
        self.assertEqual(job.source, RefreshSource.API)
        self.assertEqual(job.payload["api_token_id"], self.token.pk)

        status = self.get(body["job"])
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["status"], "refreshed")
        self.assertNotIn("raw_excerpt", status.json())

    def test_job_not_started_reports_processing(self):
        # No on_commit execution: the job stays queued.
        resp = self.client.post(
            URL,
            data=json.dumps({"phone": PHONE}),
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(resp.status_code, 202)
        status = self.get(resp.json()["job"])
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json(), {"status": "processing", "job": resp.json()["job"]})

    def test_token_secret_not_stored_or_logged(self):
        records: list[logging.LogRecord] = []

        class _Collect(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = _Collect(level=logging.DEBUG)
        root = logging.getLogger()
        old_level = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        try:
            resp = self.post()
            self.post(raw="wrong-" + self.raw)
        finally:
            root.removeHandler(handler)
            root.setLevel(old_level)
        for record in records:
            self.assertNotIn(self.raw, record.getMessage())
        job = ProviderJob.objects.get(pk=resp.json()["job"])
        self.assertNotIn(self.raw, json.dumps(job.payload))
        self.assertNotIn(self.raw, json.dumps(job.result))


class OwnershipTests(_ApiTestCase):
    def setUp(self):
        super().setUp()
        self.other, self.other_raw = make_token("other")

    def test_other_token_gets_unknown_payload_404(self):
        job_id = self.post().json()["job"]
        resp = self.get(job_id, raw=self.other_raw)
        self.assertEqual(resp.status_code, 404)
        unknown = self.get(uuid.uuid4())
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(resp.json(), unknown.json())
        self.assertEqual(self.get(job_id).status_code, 200)

    def test_garbage_job_id_is_not_a_route(self):
        resp = self.client.get(f"{URL}not-a-uuid/", **self.auth())
        self.assertEqual(resp.status_code, 404)

    def test_web_job_is_not_visible_through_api(self):
        ticket = submit_refresh(PHONE, ip=None, source=RefreshSource.WEB)
        resp = self.get(ticket.job_id)
        self.assertEqual(resp.status_code, 404)

    def test_same_token_shares_its_inflight_job(self):
        first = self.post(run_jobs=False).json()["job"]
        second = self.post(run_jobs=False).json()["job"]
        self.assertEqual(first, second)
        self.assertEqual(ProviderJob.objects.count(), 1)

    def test_other_token_does_not_join_inflight_job(self):
        first = self.post(run_jobs=False).json()["job"]
        second = self.post(raw=self.other_raw, run_jobs=False).json()["job"]
        self.assertNotEqual(first, second)
        self.assertEqual(self.get(second, raw=self.other_raw).status_code, 200)
        self.assertEqual(self.get(second).status_code, 404)

    def test_api_submit_does_not_join_web_job(self):
        web = submit_refresh(PHONE, ip=None, source=RefreshSource.WEB)
        mine = self.post(run_jobs=False).json()["job"]
        self.assertNotEqual(web.job_id, mine)

    def test_web_submit_does_not_join_token_job(self):
        mine = self.post(run_jobs=False).json()["job"]
        web = submit_refresh(PHONE, ip=None, source=RefreshSource.WEB)
        self.assertNotEqual(web.job_id, mine)
        public_api = submit_refresh(PHONE, ip=None, source=RefreshSource.API)
        self.assertNotEqual(public_api.job_id, mine)


class RateLimitTests(_ApiTestCase):
    def test_per_token_rate_limit_429(self):
        other, other_raw = make_token("other")
        for _ in range(60):
            self.assertEqual(self.post({"phone": "123"}).status_code, 200)
        resp = self.post({"phone": "123"})
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.json(), {"error": "rate_limited"})
        self.assertEqual(resp["Retry-After"], "60")
        # Another token has its own budget.
        self.assertEqual(self.post({"phone": "123"}, raw=other_raw).status_code, 200)

    def test_status_polling_has_its_own_budget(self):
        for _ in range(60):
            self.assertEqual(self.post({"phone": "123"}).status_code, 200)
        self.assertEqual(self.post({"phone": "123"}).status_code, 429)
        self.assertEqual(self.get(uuid.uuid4()).status_code, 404)

    def test_status_rate_limit_429(self):
        with patch("providers.api.views.STATUS_LIMIT_PER_MINUTE", 3):
            codes = [self.get(uuid.uuid4()).status_code for _ in range(5)]
        self.assertEqual(codes, [404, 404, 404, 429, 429])


class ScopeFormTests(TestCase):
    def test_scope_registry_has_refresh(self):
        self.assertEqual([code for code, _ in API_SCOPES], ["refresh"])

    def test_form_saves_selected_scopes(self):
        form = ApiTokenForm({"name": "shop", "scopes": ["refresh"]})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save(commit=False).scopes, "refresh")

    def test_form_requires_a_scope_and_rejects_unknown(self):
        self.assertFalse(ApiTokenForm({"name": "shop", "scopes": []}).is_valid())
        self.assertFalse(ApiTokenForm({"name": "shop", "scopes": ["root"]}).is_valid())

    def test_form_prechecks_refresh_by_default(self):
        html = str(ApiTokenForm()["scopes"])
        self.assertIn('value="refresh"', html)
        self.assertRegex(html, r'value="refresh"[^>]*checked|checked[^>]*value="refresh"')


class PublicEndpointScopeTests(TestCase):
    def setUp(self):
        get_redis().flushall()
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

    def _post(self, raw):
        return self.client.post(
            reverse("phone_refresh:public_api"),
            data=json.dumps({"phone": "123"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {raw}",
        )

    def test_public_api_requires_refresh_scope(self):
        _, raw = make_token("charge-only", scopes="charge")
        resp = self._post(raw)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json(), {"error": "insufficient_scope"})

    def test_public_api_still_works_with_default_token(self):
        _, raw = make_token("default")
        self.assertEqual(self._post(raw).status_code, 200)


class TokenCreateViewTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        from accounts.models import UserProfile

        admin = get_user_model().objects.create_user("scope_admin", password="x")
        UserProfile.objects.update_or_create(
            user=admin,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        self.client.force_login(admin)
        self.url = reverse("phone_refresh:api_token_create")

    def test_form_shows_scope_checkbox(self):
        resp = self.client.get(self.url)
        self.assertContains(resp, 'name="scopes"')
        self.assertContains(resp, 'value="refresh"')

    def test_create_stores_selected_scopes(self):
        resp = self.client.post(self.url, {"name": "shop", "scopes": ["refresh"]})
        self.assertEqual(resp.status_code, 200)  # shows the raw token once
        self.assertEqual(ApiToken.objects.get(name="shop").scopes, "refresh")

    def test_create_without_scope_is_rejected(self):
        resp = self.client.post(self.url, {"name": "shop"})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(ApiToken.objects.filter(name="shop").exists())
