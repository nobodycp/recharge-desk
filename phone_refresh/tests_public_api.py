"""Public refresh API auth gate tests."""
from __future__ import annotations

import hashlib
import json
from unittest.mock import patch

from django.test import Client, RequestFactory, TestCase
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
