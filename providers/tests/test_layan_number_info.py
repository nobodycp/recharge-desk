import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from providers.adapters.layan.adapter import LayanAdapter
from providers.adapters.layan.number_info import parse_number_info
from providers.contracts import AuthRequired, NumberInfo, PermanentError
from providers.crypto import encrypt
from providers.models import ProviderConnection
from providers.sessions import SessionStore
from sales.number_check import card_context


SUBSCRIPTION = {
    "number": "0512608436",
    "simNumber": "89972096000016411836",
    "isEsim": True,
    "endDate": "18/11/2026 16:16",
    "package": {
        "name": "حزمة 500 جيجا",
        "internet": "unlimited",
        "messages": "3000",
        "minutes": "3000",
        "companyName": "We",
        "cost": "30.00",
    },
}
USAGE = {
    "number": {"status": "فعال"},
    "internet_Size": 0,
    "internet_UsedMB": 1536,
    "messages_Used": 12,
    "messages_Size": 3000,
    "voice_Used": 40,
    "voice_Size": 3000,
    "package_Usage": 4,
    "simNumber": "89972096000016411836",
}


class LayanNumberInfoParseTests(TestCase):
    def test_unlimited_data_shows_what_was_used_and_no_allowance(self):
        info = parse_number_info(SUBSCRIPTION, USAGE)
        self.assertEqual(info.network, "We")
        self.assertFalse(info.is_esim)
        self.assertEqual(info.status, "فعال")
        self.assertEqual(info.plan_name, "حزمة 500 جيجا")
        self.assertEqual(info.iccid, "89972096000016411836")
        self.assertEqual(info.plan_price, Decimal("30.00"))
        self.assertEqual(info.data_used_gb, Decimal("1.50"))
        self.assertIsNone(info.data_total_gb)
        self.assertTrue(info.data_unlimited)
        self.assertEqual((info.minutes_used, info.minutes_total), (40, 3000))
        self.assertEqual((info.sms_used, info.sms_total), (12, 3000))
        self.assertEqual(info.utilization_pct, Decimal("4"))
        ends = timezone.localtime(info.ends_at)
        self.assertEqual((ends.day, ends.month, ends.hour, ends.minute), (18, 11, 16, 16))

    def test_limited_data_is_already_gigabytes_like_the_portal(self):
        usage = {**USAGE, "internet_Size": 500, "internet_UsedMB": "27.82"}
        subscription = {
            **SUBSCRIPTION,
            "package": {**SUBSCRIPTION["package"], "internet": "500G"},
            "isEsim": False,
        }
        info = parse_number_info(subscription, usage)
        self.assertEqual(info.data_used_gb, Decimal("27.82"))
        self.assertEqual(info.data_total_gb, Decimal("500.00"))
        self.assertFalse(info.data_unlimited)
        self.assertFalse(info.is_esim)

    def test_card_uses_the_end_date_for_remaining_days_and_hides_portal_cost(self):
        info = parse_number_info(SUBSCRIPTION, USAGE)
        ctx = card_context(info, None, date(2026, 11, 8), phone="0512608436")
        self.assertEqual(ctx["expiry"]["date"], "18/11/2026")
        self.assertEqual(ctx["expiry"]["days"], "10")
        self.assertEqual(ctx["status"], "فعال")
        self.assertEqual(ctx["sim_type"], "شريحة عادية")
        data = ctx["usage"][0]
        self.assertEqual(data["used"], "1.5")
        self.assertEqual(data["total"], "")
        self.assertEqual(data["unit"], "جيجا")
        self.assertTrue(data["unlimited"])
        self.assertEqual(ctx["utilization"], "")
        self.assertEqual(ctx["phone_display"], "0512608436")
        self.assertNotIn("plan_price", ctx)
        self.assertIsNone(ctx["sale"])
        self.assertEqual(ctx["sales"][2]["value"], "30 ₪")
        self.assertNotIn("30 ₪", json.dumps(ctx["share"], ensure_ascii=False))

    def test_card_sim_type_follows_our_sale_not_layan(self):
        info = NumberInfo(is_esim=True, network="We")
        electronic = SimpleNamespace(
            is_esim=True, company=SimpleNamespace(provider_key="layan"), product=None
        )
        physical = SimpleNamespace(
            is_esim=False, company=SimpleNamespace(provider_key="layan"), product=None
        )
        self.assertEqual(
            card_context(info, electronic, date(2026, 11, 8))["sim_type"],
            "شريحة إلكترونية",
        )
        self.assertEqual(
            card_context(info, physical, date(2026, 11, 8))["sim_type"],
            "شريحة عادية",
        )


class LayanNumberInfoAdapterTests(TestCase):
    def setUp(self):
        from providers.redis_conn import get_redis

        get_redis().flushall()
        connection = ProviderConnection.for_key("layan")
        connection.username = "user"
        connection.secret_encrypted = encrypt("pass")
        connection.save()
        SessionStore("layan").save(
            {"jwt": "token", "expires_at": 2_000_000_000, "logged_in_at": 1}
        )

    def test_matching_subscription_becomes_number_info(self):
        client = MagicMock()
        client.fetch_subscription.return_value = [SUBSCRIPTION]
        client.fetch_usage.return_value = USAGE
        with patch(
            "providers.adapters.layan.adapter.LayanClient.from_store", return_value=client
        ):
            info = LayanAdapter().number_info("0512608436")
        self.assertIsInstance(info, NumberInfo)
        self.assertEqual(info.plan_name, "حزمة 500 جيجا")

    def test_unknown_number_is_not_found(self):
        client = MagicMock()
        client.fetch_subscription.return_value = []
        with patch(
            "providers.adapters.layan.adapter.LayanClient.from_store", return_value=client
        ):
            with self.assertRaises(PermanentError) as caught:
                LayanAdapter().number_info("0512608436")
        self.assertEqual(str(caught.exception), "not_found")

    def test_expired_session_asks_for_login(self):
        from providers.adapters.layan.client import LayanSessionError

        client = MagicMock()
        client.fetch_subscription.side_effect = LayanSessionError("session expired")
        with patch(
            "providers.adapters.layan.adapter.LayanClient.from_store", return_value=client
        ):
            with self.assertRaises(AuthRequired):
                LayanAdapter().number_info("0512608436")
