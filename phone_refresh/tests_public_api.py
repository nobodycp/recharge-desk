"""Public refresh API auth gate tests."""
from __future__ import annotations

import hashlib
import json
from unittest.mock import patch

from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from phone_refresh.models import ApiSettings, ApiToken, RefreshSource, RefreshStatus, SiteSettings
from phone_refresh.services.refresh_service import RefreshResult
from phone_refresh.views.public import public_refresh_api


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

    def _post(self, payload: dict, *, headers: dict | None = None):
        request = self.factory.post(
            reverse("phone_refresh:public_api"),
            data=json.dumps(payload),
            content_type="application/json",
            **(headers or {}),
        )
        return public_refresh_api(request)

    @patch("phone_refresh.views.public.refresh_phone")
    def test_web_client_with_valid_bearer_token(self, refresh_mock):
        raw = "test-public-page-token-value"
        token = ApiToken.objects.create(
            name="صفحة التحديث العامة",
            token_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            prefix=raw[:8],
        )
        refresh_mock.return_value = RefreshResult(
            status=self.error_status,
            provider=None,
            message_title="",
            message_body="",
            matched_rule_id=None,
            raw_status_code=None,
            raw_excerpt="",
        )
        response = self._post(
            {"phone_number": "0591234567", "client": RefreshSource.WEB},
            headers={"HTTP_AUTHORIZATION": f"Bearer {raw}"},
        )
        self.assertEqual(response.status_code, 200)
        refresh_mock.assert_called_once()
        token.refresh_from_db()
        self.assertIsNotNone(token.last_used_at)

    @patch("phone_refresh.views.public.refresh_phone")
    def test_web_client_bypasses_token_when_anonymous_page_allowed(self, refresh_mock):
        ApiSettings.objects.filter(pk=1).update(allow_anonymous_test_page=True)
        refresh_mock.return_value = RefreshResult(
            status=self.error_status,
            provider=None,
            message_title="",
            message_body="",
            matched_rule_id=None,
            raw_status_code=None,
            raw_excerpt="",
        )
        response = self._post(
            {"phone_number": "0591234567", "client": RefreshSource.WEB},
        )
        self.assertEqual(response.status_code, 200)
        refresh_mock.assert_called_once()

    def test_api_client_requires_token(self):
        response = self._post({"phone_number": "0591234567"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(json.loads(response.content), {"error": "missing_token"})

    def test_web_client_requires_token_when_anonymous_page_disabled(self):
        response = self._post(
            {"phone_number": "0591234567", "client": RefreshSource.WEB},
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

    @patch("phone_refresh.views.public.refresh_phone")
    def test_uses_cf_connecting_ip_for_refresh_log(self, refresh_mock):
        refresh_mock.return_value = RefreshResult(
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
            data=json.dumps({"phone_number": "0591234567", "client": RefreshSource.WEB}),
            content_type="application/json",
            REMOTE_ADDR="203.0.113.10",
            HTTP_CF_CONNECTING_IP="198.51.100.20",
            HTTP_X_FORWARDED_FOR="198.51.100.30, 203.0.113.1",
        )
        response = public_refresh_api(request)
        self.assertEqual(response.status_code, 200)
        refresh_mock.assert_called_once()
        self.assertEqual(refresh_mock.call_args.kwargs["ip"], "198.51.100.20")

    @patch("phone_refresh.views.public.refresh_phone")
    def test_rate_limit_uses_same_client_ip(self, refresh_mock):
        ApiSettings.objects.filter(pk=1).update(rate_limit_per_minute=1)
        refresh_mock.return_value = RefreshResult(
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
        payload = json.dumps({"phone_number": "0591234567", "client": RefreshSource.WEB})

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
