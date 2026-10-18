from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase

from providers.adapters.layan import charge as layan_charge
from providers.adapters.layan.client import LayanClient
from providers.accounts import Credentials


def _creds() -> Credentials:
    return Credentials("u", "p", "JBSWY3DPEHPK3PXP", {})


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
