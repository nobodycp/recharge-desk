"""Tests for core.http_utils."""
from __future__ import annotations

from django.http import HttpRequest
from django.test import SimpleTestCase, override_settings

from core.http_utils import get_client_ip


def _request(**meta) -> HttpRequest:
    request = HttpRequest()
    request.META.update(meta)
    return request


class GetClientIpTests(SimpleTestCase):
    def test_returns_none_for_missing_request(self):
        self.assertIsNone(get_client_ip(None))

    @override_settings(TRUST_FORWARDED_FOR=False)
    def test_ignores_forwarded_headers_when_not_trusted(self):
        request = _request(
            REMOTE_ADDR="203.0.113.10",
            HTTP_CF_CONNECTING_IP="198.51.100.20",
            HTTP_X_FORWARDED_FOR="198.51.100.30, 203.0.113.1",
        )
        self.assertEqual(get_client_ip(request), "203.0.113.10")

    @override_settings(TRUST_FORWARDED_FOR=True)
    def test_prefers_cf_connecting_ip_over_xff(self):
        request = _request(
            REMOTE_ADDR="203.0.113.10",
            HTTP_CF_CONNECTING_IP="198.51.100.20",
            HTTP_X_FORWARDED_FOR="198.51.100.30, 203.0.113.1",
        )
        self.assertEqual(get_client_ip(request), "198.51.100.20")

    @override_settings(TRUST_FORWARDED_FOR=True)
    def test_uses_first_xff_ip_when_cf_header_missing(self):
        request = _request(
            REMOTE_ADDR="203.0.113.10",
            HTTP_X_FORWARDED_FOR="198.51.100.30, 203.0.113.1",
        )
        self.assertEqual(get_client_ip(request), "198.51.100.30")

    @override_settings(TRUST_FORWARDED_FOR=True)
    def test_falls_back_to_remote_addr(self):
        request = _request(REMOTE_ADDR="203.0.113.10")
        self.assertEqual(get_client_ip(request), "203.0.113.10")

    @override_settings(TRUST_FORWARDED_FOR=True)
    def test_ignores_invalid_forwarded_values(self):
        request = _request(
            REMOTE_ADDR="203.0.113.10",
            HTTP_CF_CONNECTING_IP="not-an-ip",
            HTTP_X_FORWARDED_FOR="also-bad, 203.0.113.1",
        )
        self.assertEqual(get_client_ip(request), "203.0.113.10")
