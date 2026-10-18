from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import TestCase

from providers.adapters.layan import charge as layan_charge
from providers.adapters.layan.adapter import LayanAdapter
from providers.adapters.layan.client import LayanClient
from providers.accounts import Credentials
from providers.contracts import Capability, ChargeContext, ChargeResult, PermanentError


def _creds() -> Credentials:
    return Credentials("u", "p", "JBSWY3DPEHPK3PXP", {})


def _ctx(**kwargs) -> ChargeContext:
    data = dict(
        sale_id=1,
        phone="0512932676",
        expected_cost=Decimal("30.00"),
        product_name="wecom — 500GB",
        is_esim=False,
        idempotency_key="k",
        requested_at=datetime(2026, 10, 19, tzinfo=timezone.utc),
        duration_days=30,
    )
    data.update(kwargs)
    return ChargeContext(**data)


SUB = {
    "number": "0512932676",
    "endDate": "28/07/2026 14:22",
    "package": {"name": "حزمة 500 جيجا 2024", "cost": "30.00"},
}
SALES = [
    {
        "id": "cf68bc21-989a-4d3b-fce8-08de494f73d6",
        "name": "حزمة 500 جيجا 2026 / شهري / 80₪ / ثلاثة اشهر",
        "duration": "ثلاثة اشهر",
    }
]


class LayanChargeHelperTests(TestCase):
    def test_active_status_is_only_faal(self):
        self.assertTrue(layan_charge.is_active_status("فعال"))
        self.assertTrue(layan_charge.is_active_status(" فعال "))
        self.assertFalse(layan_charge.is_active_status("غير فعال"))
        self.assertFalse(layan_charge.is_active_status(""))
        self.assertFalse(layan_charge.is_active_status("موقوف"))

    def test_pick_three_month_sale(self):
        sales = [
            {"id": "a", "name": "شهري", "duration": "شهر"},
            {
                "id": "cf68bc21-989a-4d3b-fce8-08de494f73d6",
                "name": "حزمة 500 جيجا 2026 / شهري / 80₪ / ثلاثة اشهر",
                "duration": "ثلاثة اشهر",
            },
        ]
        picked = layan_charge.pick_three_month_sale(sales)
        self.assertEqual(picked["id"], "cf68bc21-989a-4d3b-fce8-08de494f73d6")
        self.assertIsNone(layan_charge.pick_three_month_sale([]))
        self.assertIsNone(layan_charge.pick_three_month_sale([{"id": "x", "duration": "شهر"}]))
        self.assertEqual(
            layan_charge.pick_three_month_sale([{"id": "y", "duration": 90}])["id"],
            "y",
        )

    def test_parse_cost_quote(self):
        self.assertEqual(layan_charge.parse_cost_quote(30), Decimal("30.00"))
        self.assertEqual(layan_charge.parse_cost_quote("80"), Decimal("80.00"))
        self.assertEqual(layan_charge.parse_cost_quote("80.5"), Decimal("80.50"))
        self.assertIsNone(layan_charge.parse_cost_quote(None))
        self.assertIsNone(layan_charge.parse_cost_quote("x"))

    def test_money_match(self):
        self.assertTrue(layan_charge.money_match(Decimal("30"), Decimal("30.00")))
        self.assertFalse(layan_charge.money_match(Decimal("30"), Decimal("80")))
        self.assertFalse(layan_charge.money_match(None, Decimal("30")))

    def test_charge_success_body(self):
        self.assertTrue(
            layan_charge.is_charge_success(200, "سيتم تشغيل الرقم في الخلفيه")
        )
        self.assertTrue(layan_charge.is_charge_success(200, "  "))
        self.assertFalse(layan_charge.is_charge_success(400, "سيتم تشغيل الرقم في الخلفيه"))
        self.assertFalse(layan_charge.is_charge_success(200, "خطأ في العملية"))


class LayanChargeClientTests(TestCase):
    def test_get_sales_by_number(self):
        client = LayanClient(credentials=_creds(), jwt="t", expires_at=2_000_000_000)
        response = MagicMock(status_code=200)
        response.json.return_value = [{"id": "s1", "duration": "ثلاثة اشهر"}]
        with patch.object(client, "_get", return_value=response) as get:
            rows = client.get_sales_by_number("0512932676")
        self.assertEqual(get.call_args.args[0], "Subscribtions/GetSalesByNumber/0512932676")
        self.assertEqual(rows[0]["id"], "s1")

    def test_get_cost_before_activate(self):
        client = LayanClient(credentials=_creds(), jwt="t", expires_at=2_000_000_000)
        response = MagicMock(status_code=200)
        response.json.return_value = 30
        with patch.object(client, "_post", return_value=response) as post:
            body = client.get_cost_before_activate("0512932676", duration=30, sale_id=None)
        self.assertEqual(post.call_args.args[0], "Deals/GetCostBeforeActivate")
        self.assertEqual(
            post.call_args.args[1],
            {"Number": "0512932676", "Duration": 30, "SaleId": None},
        )
        self.assertEqual(body, 30)

    def test_extend_and_activate(self):
        client = LayanClient(credentials=_creds(), jwt="t", expires_at=2_000_000_000)
        response = MagicMock(status_code=200)
        response.text = "سيتم تشغيل الرقم في الخلفيه"
        response.json.side_effect = ValueError("not json")
        with patch.object(client, "_post", return_value=response) as post:
            code, text = client.extend_line("0512932676", duration=30, sale_id=None)
            self.assertEqual(post.call_args.args[0], "Deals/Extend")
            self.assertTrue(post.call_args.args[1]["UserPaid"])
            self.assertEqual((code, text), (200, "سيتم تشغيل الرقم في الخلفيه"))
            code, text = client.activate_line(
                "0512932676",
                duration=30,
                sale_id=None,
                end_date="28/07/2026 14:22",
            )
            self.assertEqual(post.call_args.args[0], "Deals/ActivateLine")
            self.assertEqual(post.call_args.args[1]["endDate"], "28/07/2026 14:22")
            self.assertEqual(code, 200)


class LayanAdapterChargeTests(TestCase):
    def setUp(self):
        self.client = SimpleNamespace(
            fetch_subscription=MagicMock(return_value=[SUB]),
            fetch_usage=MagicMock(return_value={"number": {"status": "فعال"}}),
            get_cost_before_activate=MagicMock(return_value=30),
            get_sales_by_number=MagicMock(return_value=SALES),
            extend_line=MagicMock(return_value=(200, "سيتم تشغيل الرقم في الخلفيه")),
            activate_line=MagicMock(return_value=(200, "سيتم تشغيل الرقم في الخلفيه")),
        )

    def _charge(self, ctx=None, **client_attrs):
        for key, value in client_attrs.items():
            setattr(self.client, key, value)
        with patch.object(LayanAdapter, "_stored_client", return_value=self.client):
            return LayanAdapter().charge(ctx or _ctx())

    def test_active_month_extends_after_price_match(self):
        outcome = self._charge()
        self.assertEqual(outcome.result, ChargeResult.SUCCESS)
        self.assertEqual(outcome.provider_cost, Decimal("30.00"))
        self.client.extend_line.assert_called_once()
        kwargs = self.client.extend_line.call_args.kwargs
        self.assertEqual(kwargs["duration"], 30)
        self.assertIsNone(kwargs["sale_id"])
        self.client.activate_line.assert_not_called()

    def test_inactive_month_activates_with_end_date(self):
        self.client.fetch_usage.return_value = {"number": {"status": "غير فعال"}}
        outcome = self._charge()
        self.assertEqual(outcome.result, ChargeResult.SUCCESS)
        self.client.activate_line.assert_called_once()
        kwargs = self.client.activate_line.call_args.kwargs
        self.assertEqual(kwargs["end_date"], "28/07/2026 14:22")
        self.client.extend_line.assert_not_called()

    def test_three_months_uses_sale_id(self):
        self.client.get_cost_before_activate.return_value = 80
        outcome = self._charge(_ctx(duration_days=90, expected_cost=Decimal("80.00")))
        self.assertEqual(outcome.result, ChargeResult.SUCCESS)
        kwargs = self.client.extend_line.call_args.kwargs
        self.assertIsNone(kwargs["duration"])
        self.assertEqual(kwargs["sale_id"], "cf68bc21-989a-4d3b-fce8-08de494f73d6")
        self.client.get_cost_before_activate.assert_called_with(
            "0512932676",
            duration=None,
            sale_id="cf68bc21-989a-4d3b-fce8-08de494f73d6",
        )

    def test_three_months_without_promo_does_not_charge(self):
        self.client.get_sales_by_number.return_value = []
        with self.assertRaises(PermanentError) as caught:
            self._charge(_ctx(duration_days=90, expected_cost=Decimal("80.00")))
        self.assertEqual(str(caught.exception), "period_unavailable")
        self.client.extend_line.assert_not_called()
        self.client.activate_line.assert_not_called()

    def test_price_mismatch_does_not_charge(self):
        self.client.get_cost_before_activate.return_value = 80
        with self.assertRaises(PermanentError) as caught:
            self._charge()
        self.assertEqual(str(caught.exception), "price_mismatch")
        self.client.extend_line.assert_not_called()
        self.client.activate_line.assert_not_called()

    def test_unknown_number_is_not_found(self):
        self.client.fetch_subscription.return_value = []
        with self.assertRaises(PermanentError) as caught:
            self._charge()
        self.assertEqual(str(caught.exception), "not_found")

    def test_capability_includes_charge(self):
        self.assertIn(Capability.CHARGE, LayanAdapter.capabilities)
