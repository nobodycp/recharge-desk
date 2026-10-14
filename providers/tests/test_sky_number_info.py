import json
import time
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

import requests
from django.test import SimpleTestCase
from django.utils import timezone

from providers import operations
from providers.accounts import Credentials
from providers.adapters.sky.adapter import SkyAdapter
from providers.adapters.sky.client import (
    SkySalesClient,
    SkySalesSessionError,
    proxy_fingerprint,
)
from providers.adapters.sky.number_info import matches_phone, parse_number_info
from providers.contracts import (
    AuthRequired,
    BaseAdapter,
    Capability,
    NumberInfo,
    PermanentError,
    TransientError,
)
from providers.redis_conn import get_redis
from providers.sessions import SessionStore

SEARCH = "providers.adapters.sky.client.SkySalesClient.search_subscriber"

ROW_ESIM = {
    "ne_id": "0525737739",
    "msisdn": "972525737739",
    "subscriber_name": "عميل تجريبي",
    "iccid": "8997000000000000001",
    "mno_name": "سلكوم",
    "mno_id": "1",
    "esim": "esim",
    "status_name": "مفعل",
    "status_id": "301",
    "rate_plan_name": "حملة رمضان 400 جيجا سلكوم",
    "rate_plan_type_name": "Renters",
    "rate_plan_end_date": "14/11/2026",
    "rate_plan_end_date_time": "13/11/2026 23:30",
    "days_to_end_of_plan": "30",
    "utilization": "1.52",
    "allocated_mb": "409600",
    "allocated_minutes": "5000",
    "allocated_sms": "5000",
    "total_data_in_billing_cycle": "6.08",
    "total_voice_in_billing_cycle": "0",
    "total_sms_in_billing_cycle": "0",
    "rate_plan_price": "29",
    "subscriber_price": "40",
    "rate_plan_id": "396",
}

ROW_PHYSICAL = {
    "ne_id": "0555544071",
    "msisdn": "972555544071",
    "subscriber_name": "عميل آخر",
    "iccid": "8997000000000000002",
    "mno_name": "سلكوم",
    "esim": "",
    "status_name": "مفعل",
    "rate_plan_name": "AL سلكوم 400 جيجا",
    "rate_plan_end_date": "13/11/2026",
    "rate_plan_end_date_time": "12/11/2026 23:30",
    "utilization": "0.00",
    "allocated_mb": "409600",
    "allocated_minutes": "5000",
    "allocated_sms": "5000",
    "total_data_in_billing_cycle": "0",
    "total_voice_in_billing_cycle": "0",
    "total_sms_in_billing_cycle": "0",
    "rate_plan_price": "41",
    "commitment_end_date": None,
}


class ParseNumberInfoTests(SimpleTestCase):
    def test_esim_row(self):
        info = parse_number_info(ROW_ESIM)
        self.assertIs(info.is_esim, True)
        self.assertEqual(info.network, "سلكوم")
        self.assertEqual(info.status, "مفعل")
        self.assertEqual(info.plan_name, "حملة رمضان 400 جيجا سلكوم")
        self.assertEqual(info.iccid, "8997000000000000001")
        self.assertEqual(info.plan_price, Decimal("29"))
        self.assertEqual(info.data_used_gb, Decimal("6.08"))
        self.assertEqual(info.data_total_gb, Decimal("400"))
        self.assertEqual((info.minutes_used, info.minutes_total), (0, 5000))
        self.assertEqual((info.sms_used, info.sms_total), (0, 5000))
        self.assertEqual(info.utilization_pct, Decimal("1.52"))

    def test_ends_at_is_aware_in_project_timezone(self):
        info = parse_number_info(ROW_ESIM)
        self.assertTrue(timezone.is_aware(info.ends_at))
        local = timezone.localtime(info.ends_at)
        self.assertEqual(
            (local.year, local.month, local.day, local.hour, local.minute),
            (2026, 11, 13, 23, 30),
        )
        self.assertEqual(local.tzinfo, timezone.get_default_timezone())

    def test_physical_row(self):
        info = parse_number_info(ROW_PHYSICAL)
        self.assertIs(info.is_esim, False)
        self.assertEqual(info.plan_price, Decimal("41"))
        self.assertEqual(info.data_used_gb, Decimal("0"))
        self.assertEqual(info.utilization_pct, Decimal("0.00"))
        local = timezone.localtime(info.ends_at)
        self.assertEqual((local.month, local.day, local.hour), (11, 12, 23))

    def test_esim_flag_is_case_insensitive(self):
        self.assertTrue(parse_number_info({"esim": "ESIM"}).is_esim)
        self.assertTrue(parse_number_info({"esim": " eSim "}).is_esim)
        self.assertFalse(parse_number_info({"esim": None}).is_esim)
        self.assertFalse(parse_number_info({"esim": "null"}).is_esim)

    def test_garbage_values_become_none(self):
        row = dict(ROW_ESIM)
        row.update(
            {
                "rate_plan_price": "abc",
                "rate_plan_end_date_time": "garbage",
                "rate_plan_end_date": "also bad",
                "total_data_in_billing_cycle": "null",
                "allocated_mb": "",
                "total_voice_in_billing_cycle": "x",
                "allocated_minutes": None,
                "total_sms_in_billing_cycle": "1.5.2",
                "allocated_sms": "NaN",
                "utilization": "Infinity",
            }
        )
        info = parse_number_info(row)
        self.assertIsNone(info.plan_price)
        self.assertIsNone(info.ends_at)
        self.assertIsNone(info.data_used_gb)
        self.assertIsNone(info.data_total_gb)
        self.assertIsNone(info.minutes_used)
        self.assertIsNone(info.minutes_total)
        self.assertIsNone(info.sms_used)
        self.assertIsNone(info.sms_total)
        self.assertIsNone(info.utilization_pct)
        # untouched fields still parse
        self.assertEqual(info.network, "سلكوم")

    def test_non_finite_decimals_rejected(self):
        for bad in ("NaN", "nan", "Infinity", "-Infinity", "inf", "sNaN"):
            with self.subTest(bad=bad):
                info = parse_number_info(
                    {
                        "rate_plan_price": bad,
                        "utilization": bad,
                        "total_data_in_billing_cycle": bad,
                        "allocated_mb": bad,
                    }
                )
                self.assertIsNone(info.plan_price)
                self.assertIsNone(info.utilization_pct)
                self.assertIsNone(info.data_used_gb)
                self.assertIsNone(info.data_total_gb)

    def test_empty_row_gives_empty_info(self):
        info = parse_number_info({})
        self.assertEqual(info, NumberInfo())
        self.assertEqual(info.network, "")
        self.assertIsNone(info.ends_at)

    def test_missing_datetime_falls_back_to_end_date(self):
        for missing in (None, "", "null", "bad"):
            with self.subTest(missing=missing):
                row = dict(ROW_ESIM, rate_plan_end_date_time=missing)
                info = parse_number_info(row)
                local = timezone.localtime(info.ends_at)
                self.assertEqual(
                    (local.year, local.month, local.day, local.hour, local.minute),
                    (2026, 11, 14, 0, 0),
                )

    def test_datetime_wins_over_end_date(self):
        local = timezone.localtime(parse_number_info(ROW_ESIM).ends_at)
        self.assertEqual(local.day, 13)

    def test_int_parsing(self):
        info = parse_number_info(
            {
                "total_voice_in_billing_cycle": "12",
                "allocated_minutes": "5000.0",
                "total_sms_in_billing_cycle": " 7 ",
                "allocated_sms": "5000.7",
            }
        )
        self.assertEqual(info.minutes_used, 12)
        self.assertEqual(info.minutes_total, 5000)
        self.assertEqual(info.sms_used, 7)
        # a fractional count is not an int: don't invent a number
        self.assertIsNone(info.sms_total)

    def test_absurd_exponents_rejected(self):
        info = parse_number_info({"allocated_minutes": "1e999999", "utilization": "1E+400"})
        self.assertIsNone(info.minutes_total)
        self.assertIsNone(info.utilization_pct)

    def test_numeric_json_values(self):
        info = parse_number_info(
            {
                "rate_plan_price": 29,
                "allocated_mb": 409600,
                "allocated_minutes": 5000,
                "utilization": 1.52,
                "total_data_in_billing_cycle": 6.08,
            }
        )
        self.assertEqual(info.plan_price, Decimal("29"))
        self.assertEqual(info.data_total_gb, Decimal("400"))
        self.assertEqual(info.minutes_total, 5000)
        self.assertEqual(info.utilization_pct, Decimal("1.52"))
        self.assertEqual(info.data_used_gb, Decimal("6.08"))

    def test_uppercase_keys(self):
        info = parse_number_info({"MNO_NAME": "سلكوم", "ESIM": "esim"})
        self.assertEqual(info.network, "سلكوم")
        self.assertTrue(info.is_esim)

    def test_data_total_is_quantized(self):
        info = parse_number_info({"allocated_mb": "1000"})
        self.assertEqual(info.data_total_gb, Decimal("0.98"))


class NumberInfoRoundTripTests(SimpleTestCase):
    def test_round_trip_exact(self):
        for row in (ROW_ESIM, ROW_PHYSICAL, {}):
            with self.subTest(row=row.get("ne_id")):
                info = parse_number_info(row)
                again = NumberInfo.from_dict(info.to_dict())
                self.assertEqual(again, info)

    def test_to_dict_is_json_safe(self):
        data = parse_number_info(ROW_ESIM).to_dict()
        text = json.dumps(data)
        self.assertEqual(data["plan_price"], "29")
        self.assertEqual(data["data_used_gb"], "6.08")
        self.assertIsInstance(data["ends_at"], str)
        self.assertEqual(NumberInfo.from_dict(json.loads(text)), parse_number_info(ROW_ESIM))

    def test_ends_at_keeps_offset(self):
        data = parse_number_info(ROW_ESIM).to_dict()
        parsed = datetime.fromisoformat(data["ends_at"])
        self.assertIsNotNone(parsed.utcoffset())

    def test_from_dict_tolerates_missing_and_none(self):
        self.assertEqual(NumberInfo.from_dict({}), NumberInfo())
        info = NumberInfo.from_dict({"network": "x", "plan_price": None, "ends_at": None})
        self.assertEqual(info.network, "x")
        self.assertIsNone(info.plan_price)

    def test_from_dict_ignores_unknown_keys(self):
        self.assertEqual(NumberInfo.from_dict({"bogus": 1}), NumberInfo())


class MatchesPhoneTests(SimpleTestCase):
    def test_ne_id_exact(self):
        self.assertTrue(matches_phone(ROW_ESIM, "0525737739"))

    def test_msisdn_suffix(self):
        self.assertTrue(matches_phone({"msisdn": "972525737739"}, "0525737739"))
        self.assertTrue(matches_phone({"ne_id": "", "msisdn": "972525737739"}, "525737739"))

    def test_other_number_does_not_match(self):
        self.assertFalse(matches_phone(ROW_ESIM, "0555544071"))
        self.assertFalse(matches_phone(ROW_PHYSICAL, "0525737739"))

    def test_partial_ne_id_does_not_match(self):
        self.assertFalse(matches_phone({"ne_id": "0525737739"}, "052573773"))

    def test_empty_phone_or_row(self):
        self.assertFalse(matches_phone({}, "0525737739"))
        self.assertFalse(matches_phone(ROW_ESIM, ""))
        self.assertFalse(matches_phone(ROW_ESIM, "0"))
        self.assertFalse(matches_phone(ROW_ESIM, "   "))

    def test_uppercase_keys(self):
        self.assertTrue(matches_phone({"NE_ID": "0525737739"}, "0525737739"))


def _ready_session():
    return {
        "jwt_token": "jwt",
        "session_id": "sid",
        "eot_user_id": 1550,
        "user_name": "user",
        "pending_otp": False,
        "cookies": {},
        "bound_ip": "",
        "proxy_fingerprint": proxy_fingerprint(""),
        "logged_in_at": time.time(),
    }


class SkyAdapterNumberInfoTests(SimpleTestCase):
    def setUp(self):
        get_redis().flushall()
        SessionStore("sky").save(_ready_session())
        patcher = patch(
            "providers.adapters.sky.adapter.get_credentials",
            return_value=Credentials("user", "pass", "", {}),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_capability_declared(self):
        self.assertIn(Capability.NUMBER_INFO, SkyAdapter.capabilities)
        self.assertEqual(Capability.NUMBER_INFO.value, "number_info")

    def test_base_adapter_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            BaseAdapter().number_info("0525737739")

    @patch(SEARCH)
    def test_matching_row_is_parsed(self, search):
        search.return_value = ROW_ESIM
        info = SkyAdapter().number_info("0525737739")
        search.assert_called_once_with("0525737739")
        self.assertEqual(info, parse_number_info(ROW_ESIM))

    @patch(SEARCH)
    def test_phone_is_stripped(self, search):
        search.return_value = ROW_ESIM
        SkyAdapter().number_info(" 0525737739 ")
        search.assert_called_once_with("0525737739")

    @patch(SEARCH)
    def test_non_matching_row_is_not_found(self, search):
        # search_subscriber falls back to rows[0] when nothing matches exactly.
        search.return_value = ROW_PHYSICAL
        with self.assertRaises(PermanentError) as ctx:
            SkyAdapter().number_info("0525737739")
        self.assertEqual(str(ctx.exception), "not_found")

    @patch(SEARCH, return_value=None)
    def test_no_row_is_not_found(self, _search):
        with self.assertRaises(PermanentError) as ctx:
            SkyAdapter().number_info("0525737739")
        self.assertEqual(str(ctx.exception), "not_found")

    @patch(SEARCH, side_effect=SkySalesSessionError("REJECTED on /x: {}"))
    def test_session_error_raises_auth_required(self, _search):
        with self.assertRaises(AuthRequired):
            SkyAdapter().number_info("0525737739")

    @patch(SEARCH, side_effect=requests.ConnectionError("down"))
    def test_connection_error_is_transient(self, _search):
        with self.assertRaises(TransientError) as ctx:
            SkyAdapter().number_info("0525737739")
        self.assertEqual(str(ctx.exception), "connection_error")

    def test_requires_usable_session(self):
        SessionStore("sky").save(dict(_ready_session(), pending_otp=True))
        with self.assertRaises(AuthRequired):
            SkyAdapter().number_info("0525737739")

    def test_uses_stored_client(self):
        with patch.object(
            SkySalesClient, "search_subscriber", return_value=ROW_ESIM
        ) as search:
            SkyAdapter().number_info("0525737739")
        search.assert_called_once()


class NumberInfoMessageTests(SimpleTestCase):
    def test_not_found_message(self):
        result = operations.not_found("number_info")
        self.assertEqual(result.outcome, "failed")
        self.assertEqual(result.error_code, "not_found")
        self.assertEqual(result.message, "فشل فحص الرقم — الرقم غير موجود لدى المزوّد")

    def test_success_message(self):
        self.assertEqual(operations.success("number_info").message, "تمّ فحص الرقم")

    def test_labels(self):
        self.assertEqual(operations.SERVICE_LABELS["number_info"], "فحص الرقم")
        self.assertEqual(operations.CAUSE_LABELS["not_found"], "الرقم غير موجود لدى المزوّد")

    def test_refresh_not_found_reject_reason_unchanged(self):
        self.assertEqual(
            operations.classify_code("refresh", "not_found").message,
            "فشل التحديث — رفض المزوّد: الرقم غير موجود",
        )
