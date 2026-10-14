from unittest.mock import patch

from django.test import TestCase

from phone_refresh.services.refresh_service import refresh_phone
from providers.contracts import RawResponse


class RefreshServiceProviderCallTests(TestCase):
    def test_unknown_provider_is_no_provider_error(self):
        result = refresh_phone(
            "0529000000",
            internal_test=True,
            forced_provider="nope",
            bypass_provider_off=True,
        )
        self.assertEqual(result.status.code, "error")
        self.assertEqual(result.error, "no_provider:nope")

    @patch("phone_refresh.services.refresh_service.provider_service.refresh")
    def test_calls_provider_service_and_keeps_raw_error_handling(self, refresh):
        refresh.return_value = RawResponse(
            text="", json=None, status_code=0, error="connection_error"
        )
        result = refresh_phone(
            "0529000000",
            internal_test=True,
            forced_provider="sky",
            bypass_provider_off=True,
        )
        refresh.assert_called_once_with("sky", "0529000000")
        self.assertEqual(result.status.code, "error")
        self.assertEqual(result.error, "connection_error")

    @patch(
        "phone_refresh.services.refresh_service.provider_service.refresh",
        side_effect=KeyError("reason"),
    )
    def test_keyerror_inside_provider_is_generic_error_not_no_provider(self, refresh):
        with self.assertLogs("phone_refresh.services.refresh_service", level="ERROR") as logs:
            result = refresh_phone(
                "0529000000",
                internal_test=True,
                forced_provider="sky",
                bypass_provider_off=True,
            )
        refresh.assert_called_once_with("sky", "0529000000")
        self.assertEqual(result.status.code, "error")
        self.assertFalse((result.error or "").startswith("no_provider:"))
        self.assertIn("Traceback", logs.output[0])
