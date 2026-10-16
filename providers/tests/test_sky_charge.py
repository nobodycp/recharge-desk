"""Sky top-up: period days, price match, one task, and follow-up."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from django.utils import timezone

from providers.adapters.sky.adapter import SkyAdapter
from providers.adapters.sky.topup import find_period, period_days, task_settled
from providers.contracts import ChargeContext, ChargeResult, PermanentError

PHONE = "0555544071"
SIM = {
    "ID": "511677",
    "NE_ID": PHONE,
    "STATUS_ID": "301",
    "STATUS_NAME": "مفعل",
    "RATE_PLAN_ID": "579",
    "RATE_PLAN_NAME": "AL سلكوم 400 جيجا",
    "SUB_CUSTOMER_ID": "1481",
    "MNO_ID": "1",
    "MNO_NAME": "سلكوم",
    "ICCID": "8997",
    "CUSTOMER_ID": "1492",
    "SUBSCRIBER_NAME": "عميل",
    "SUBSCRIBER_PRICE": "70",
    "AUTO_RENEW": "0",
}
PERIODS = [
    {"ID": "3", "PLAN_PERIOD": "3 يوم", "AMOUNT": "3", "PERIOD_FACTOR": "1"},
    {"ID": "9", "PLAN_PERIOD": "1 شهر", "AMOUNT": "1", "PERIOD_FACTOR": "30"},
    {"ID": "10", "PLAN_PERIOD": "2 شهر", "AMOUNT": "2", "PERIOD_FACTOR": "30"},
    {"ID": "15", "PLAN_PERIOD": "12 عام", "AMOUNT": "12", "PERIOD_FACTOR": "30", "PERIOD_NAME": "months"},
]


def _ctx(**overrides):
    data = dict(
        sale_id=1,
        phone=PHONE,
        expected_cost=Decimal("4.10"),
        product_name="سلكوم",
        is_esim=False,
        idempotency_key="k",
        requested_at=timezone.now(),
        duration_days=3,
    )
    data.update(overrides)
    return ChargeContext(**data)


class PeriodTests(SimpleTestCase):
    def test_days_are_amount_times_factor(self):
        self.assertEqual(period_days(PERIODS[0]), 3)
        self.assertEqual(period_days(PERIODS[1]), 30)
        self.assertEqual(period_days(PERIODS[2]), 60)
        self.assertEqual(period_days(PERIODS[3]), 360)

    def test_find_period_needs_one_exact_length(self):
        self.assertEqual(find_period(PERIODS, 3)["ID"], "3")
        self.assertEqual(find_period(PERIODS, 60)["ID"], "10")
        self.assertIsNone(find_period(PERIODS, 10))
        self.assertIsNone(find_period(PERIODS, 0))

    def test_task_status(self):
        self.assertEqual(task_settled({"STATUS_ID": "2", "STATUS_NAME": "مهمة تم تنفيذها "}), "success")
        self.assertEqual(task_settled({"STATUS_ID": "8", "STATUS_NAME": "مهمة ملغاة"}), "failed")
        self.assertEqual(task_settled({"STATUS_ID": "1", "STATUS_NAME": "قيد التنفيذ"}), "open")
        self.assertEqual(task_settled(None), "open")


class _Client:
    def __init__(self, cost="-4.10", periods=None, create_id="4882603"):
        self.cost = cost
        self.periods = PERIODS if periods is None else periods
        self.create_id = create_id
        self.calls = []
        self.session = SimpleNamespace(eot_user_id=7)

    def is_session_usable(self):
        return True

    def search_subscriber(self, phone):
        return dict(SIM) if phone == PHONE else None

    def load_global_sub_customer_id(self):
        return "1492"

    def generic_api(self, name, args):
        self.calls.append(name)
        if name == "GetRatePlanPeriods":
            return {"result": "SUCCESS", "data": self.periods}
        if name == "GetSubscriberCosts":
            return {"result": "SUCCESS", "data": [{
                "ORDER_COST": self.cost, "ORDER_REFUND": "0", "ORDER_TOTAL_COST": self.cost,
            }]}
        if name == "ValidateTaskDataBeforeCreate":
            return {"result": "SUCCESS", "data": []}
        if name == "GetSubscriberStatus":
            return {"result": "SUCCESS", "data": [{"STATUS_ID": "301"}]}
        if name == "CreateNewTaskWithBalanceCheck":
            return {"result": "SUCCESS", "data": [{"ID": self.create_id}]}
        if name == "GetTaskDetails":
            return {"result": "SUCCESS", "data": [{"STATUS_ID": "2", "STATUS_NAME": "مهمة تم تنفيذها"}]}
        return {"result": "FAILED", "data": []}


class SkyChargeTests(SimpleTestCase):
    def _charge(self, client, ctx=None):
        with patch(
            "providers.adapters.sky.adapter.SkySalesClient.from_store", return_value=client
        ), patch("providers.adapters.sky.adapter.get_credentials", return_value=object()):
            return SkyAdapter().charge(ctx or _ctx())

    def test_three_days_charges_once_at_the_prorated_price(self):
        client = _Client()
        outcome = self._charge(client)
        self.assertEqual(outcome.result, ChargeResult.SUCCESS)
        self.assertEqual(outcome.provider_ref, "4882603")
        self.assertEqual(outcome.provider_cost, Decimal("4.10"))
        self.assertEqual(client.calls.count("CreateNewTaskWithBalanceCheck"), 1)

    def test_price_mismatch_sends_no_task(self):
        client = _Client(cost="-41.00")
        with self.assertRaises(PermanentError) as caught:
            self._charge(client)
        self.assertEqual(str(caught.exception), "price_mismatch")
        self.assertNotIn("CreateNewTaskWithBalanceCheck", client.calls)

    def test_missing_period_sends_no_task(self):
        client = _Client()
        with self.assertRaises(PermanentError) as caught:
            self._charge(client, _ctx(duration_days=10))
        self.assertEqual(str(caught.exception), "period_unavailable")
        self.assertNotIn("GetSubscriberCosts", client.calls)

    def test_unknown_number_sends_no_task(self):
        client = _Client()
        with self.assertRaises(PermanentError) as caught:
            self._charge(client, _ctx(phone="0599990000"))
        self.assertEqual(str(caught.exception), "not_found")
        self.assertEqual(client.calls, [])

    def test_two_months_is_sixty_days(self):
        client = _Client(cost="-82.00")
        outcome = self._charge(client, _ctx(duration_days=60, expected_cost=Decimal("82.00")))
        self.assertEqual(outcome.result, ChargeResult.SUCCESS)

    def test_check_charge_reads_the_task(self):
        client = _Client()
        with patch(
            "providers.adapters.sky.adapter.SkySalesClient.from_store", return_value=client
        ), patch("providers.adapters.sky.adapter.get_credentials", return_value=object()):
            outcome = SkyAdapter().check_charge(_ctx(), "4882603")
        self.assertEqual(outcome.result, ChargeResult.SUCCESS)
        self.assertEqual(client.calls, ["GetTaskDetails"])
