from datetime import date, datetime
from decimal import Decimal
from unittest.mock import Mock, patch

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserProfile
from companies.models import Company, Product, ProductLine
from companies.views import _supports_reconcile
from providers import service
from providers.adapters.sky.adapter import SkyAdapter, _sky_errors
from providers.adapters.sky.client import (
    LOGIN_CONNECT_TIMEOUT_SECONDS,
    REPORT_READ_TIMEOUT_SECONDS,
    SkySalesClient,
    SkySalesSession,
)
from providers.adapters.sky.reconcile import parse_sky_balance_rows, reconcile_sky_report
from providers.contracts import AuthRequired, TransientError
from providers.crypto import encrypt
from providers.models import ProviderConnection, ProviderOperation
from providers.redis_conn import get_redis
from sales.models import PaymentMethod, Sale

User = get_user_model()


def _save_sky_credentials():
    connection = ProviderConnection.for_key("sky")
    connection.username = "user"
    connection.secret_encrypted = encrypt("pass")
    connection.save()


def _row(
    *,
    phone,
    op="شحن",
    note="",
    before="100",
    delta="-45.00",
    after="55.00",
    cost="45.00",
    at="2026-08-10 12:00:00",
):
    return {
        "ext_return_value_1": at,
        "ext_return_value_2": note,
        "ext_return_value_3": op,
        "ext_return_value_6": phone,
        "ext_return_value_11": before,
        "ext_return_value_12": delta,
        "ext_return_value_13": after,
        "ext_return_value_14": cost,
    }


class SkyReconcileParseTests(TestCase):
    def test_esim_two_lines_sum(self):
        rows = [
            _row(phone="0521111111", delta="-45.00", after="55.00", before="100", cost="45"),
            _row(
                phone="0521111111",
                note="esim",
                delta="-5.00",
                before="55.00",
                after="50.00",
                cost="5",
                at="2026-08-10 13:00:00",
            ),
        ]
        phones, count, anomalies, end = parse_sky_balance_rows(
            rows, period_from=date(2026, 8, 1), period_to=date(2026, 8, 31)
        )
        self.assertEqual(count, 2)
        self.assertEqual(phones["0521111111"].charges, Decimal("50"))
        self.assertEqual(end, Decimal("50.00"))
        self.assertEqual(anomalies, [])

    def test_balance_anomaly_detected(self):
        rows = [
            _row(
                phone="0522222222",
                before="100",
                delta="-45",
                after="40",
            )
        ]
        _phones, _c, anomalies, _end = parse_sky_balance_rows(
            rows, period_from=None, period_to=None
        )
        self.assertEqual(len(anomalies), 1)

    def test_settlement_pair_counts_in_deficit(self):
        company = Company.objects.create(
            name="Sky",
            opening_balance=0,
            current_balance=0,
            provider_key="sky",
        )
        rows = [
            _row(
                phone="0533333333",
                delta="-45",
                before="100",
                after="55",
                at="2026-08-05 10:00:00",
            ),
            _row(
                phone="0533333333",
                op="فصل",
                delta="40",
                before="55",
                after="95",
                cost="40",
                at="2026-08-06 10:00:00",
            ),
        ]
        result = reconcile_sky_report(
            company,
            rows,
            period_from=date(2026, 8, 1),
            period_to=date(2026, 8, 31),
            min_amount_diff=Decimal("3"),
        )
        self.assertEqual(len(result.split_settlements), 1)
        self.assertEqual(result.not_recorded, [])
        # charge 45, refund 40 => retained 5 counts toward the deficit.
        self.assertEqual(result.estimated_deficit, Decimal("5"))


class SkyTimeoutTests(TestCase):
    def test_report_fetch_uses_bounded_read_timeout(self):
        client = SkySalesClient()
        client.session = SkySalesSession(jwt_token="t", eot_user_id=7)
        with patch.object(
            SkySalesClient, "_post", return_value={"result": "SUCCESS", "data": []}
        ) as post:
            client.fetch_balance_report(date(2026, 8, 1), date(2026, 8, 31))
        self.assertEqual(
            post.call_args.kwargs["timeout"],
            (LOGIN_CONNECT_TIMEOUT_SECONDS, 40),
        )
        self.assertEqual(REPORT_READ_TIMEOUT_SECONDS, 40)

    def test_post_default_timeout_unchanged(self):
        client = SkySalesClient()
        client.session = SkySalesSession(jwt_token="t")
        response = Mock()
        response.json.return_value = {"result": "SUCCESS"}
        client.http = Mock()
        client.http.post.return_value = response
        client._post("/x", {})
        self.assertEqual(
            client.http.post.call_args.kwargs["timeout"], client._request_timeout()
        )
        client._post("/x", {}, timeout=(5, 40))
        self.assertEqual(client.http.post.call_args.kwargs["timeout"], (5, 40))

    def test_timeout_maps_to_timeout_code(self):
        with self.assertRaises(TransientError) as ctx:
            with _sky_errors():
                raise requests.ReadTimeout()
        self.assertEqual(str(ctx.exception), "timeout")
        with self.assertRaises(TransientError) as ctx:
            with _sky_errors():
                raise requests.ConnectionError()
        self.assertEqual(str(ctx.exception), "connection_error")

    def test_deficit_includes_settlements_and_positive_mismatch(self):
        company = Company.objects.create(
            name="Sky", opening_balance=0, current_balance=0, provider_key="sky"
        )
        line = ProductLine.objects.create(company=company, name="L")
        product = Product.objects.create(
            line=line,
            variant_label="C",
            cost_price=Decimal("45"),
            default_sell_price=Decimal("70"),
        )
        pm = PaymentMethod.objects.create(name="cash-deficit-test")
        user = User.objects.create_user(username="deficitmgr", password="x")
        sale = Sale.objects.create(
            company=company,
            product=product,
            reference_number="0544444444",
            payer_name="t",
            payment_method=pm,
            sell_price_actual=Decimal("70"),
            cost_price_snapshot=Decimal("45"),
            profit_snapshot=Decimal("25"),
            loss_snapshot=Decimal("0"),
            created_by=user,
            status=Sale.Status.PAID,
        )
        sale.created_at = timezone.make_aware(datetime(2026, 8, 10, 12, 0))
        sale.save(update_fields=["created_at"])
        rows = [
            # not recorded: gap 45
            _row(phone="0566666666", delta="-45", before="100", after="55", cost="45"),
            # mismatch: Sky 50 vs system 45 => gap +5
            _row(
                phone="0544444444",
                delta="-50",
                before="55",
                after="5",
                cost="50",
                at="2026-08-10 13:00:00",
            ),
            # settlement: charge 45, refund 40 => retained 5
            _row(
                phone="0533333333",
                delta="-45",
                before="105",
                after="60",
                at="2026-08-05 10:00:00",
            ),
            _row(
                phone="0533333333",
                op="فصل",
                delta="40",
                before="60",
                after="100",
                cost="40",
                at="2026-08-06 10:00:00",
            ),
        ]
        result = reconcile_sky_report(
            company,
            rows,
            period_from=date(2026, 8, 1),
            period_to=date(2026, 8, 31),
            min_amount_diff=Decimal("3"),
        )
        self.assertEqual(result.total_split_settlements, Decimal("5"))
        self.assertEqual([r.gap for r in result.amount_mismatches], [Decimal("5")])
        self.assertEqual(result.estimated_deficit, Decimal("55"))


class SkyReconcileMatchTests(TestCase):
    def setUp(self):
        _save_sky_credentials()
        self.company = Company.objects.create(
            name="Sky Co",
            opening_balance=1000,
            current_balance=800,
            provider_key="sky",
        )
        self.other = Company.objects.create(
            name="Other",
            opening_balance=0,
            current_balance=0,
            provider_key="layan",
        )
        self.line = ProductLine.objects.create(company=self.company, name="L")
        self.product = Product.objects.create(
            line=self.line,
            variant_label="Cellcom",
            cost_price=Decimal("45"),
            default_sell_price=Decimal("70"),
        )
        self.other_line = ProductLine.objects.create(company=self.other, name="OL")
        self.other_product = Product.objects.create(
            line=self.other_line,
            variant_label="W",
            cost_price=Decimal("45"),
            default_sell_price=Decimal("70"),
        )
        self.pm = PaymentMethod.objects.create(name="cash-sky-test")
        self.user = User.objects.create_user(username="skymgr", password="x")
        UserProfile.objects.update_or_create(
            user=self.user,
            defaults={
                "role": UserProfile.Role.MANAGEMENT,
                "is_active_profile": True,
            },
        )

    def _sale(self, company, product, phone, cost, day=10):
        sale = Sale.objects.create(
            company=company,
            product=product,
            reference_number=phone,
            payer_name="t",
            payment_method=self.pm,
            sell_price_actual=Decimal("70"),
            cost_price_snapshot=cost,
            profit_snapshot=Decimal("70") - cost,
            loss_snapshot=Decimal("0"),
            created_by=self.user,
            status=Sale.Status.PAID,
        )
        sale.created_at = timezone.make_aware(datetime(2026, 8, day, 12, 0))
        sale.save(update_fields=["created_at"])
        return sale

    def test_not_recorded_and_rd_only_and_mismatch(self):
        self._sale(self.company, self.product, "0544444444", Decimal("45"))
        self._sale(self.company, self.product, "0555555555", Decimal("45"))
        rows = [
            _row(phone="0544444444", delta="-50", before="100", after="50", cost="50"),
            _row(phone="0566666666", delta="-45", before="50", after="5", cost="45"),
        ]
        result = reconcile_sky_report(
            self.company,
            rows,
            period_from=date(2026, 8, 1),
            period_to=date(2026, 8, 31),
            min_amount_diff=Decimal("3"),
        )
        not_phones = {r.phone for r in result.not_recorded}
        self.assertIn("0566666666", not_phones)
        rd_only = {r.phone for r in result.rd_only}
        self.assertIn("0555555555", rd_only)
        mismatch = {r.phone for r in result.amount_mismatches}
        self.assertIn("0544444444", mismatch)
        self.assertGreater(result.estimated_deficit, 0)
        self.assertEqual(result.total_split_settlements, Decimal("0"))
        mismatch_row = next(r for r in result.amount_mismatches if r.phone == "0544444444")
        self.assertIn("50.00", mismatch_row.reason)
        self.assertIn("45.00", mismatch_row.reason)
        self.assertTrue(mismatch_row.rd_sales)
        self.assertEqual(result.action_count, 2)  # not_recorded + rd_only
        self.assertEqual(result.mismatch_count, 1)

    def test_new_sim_chip_is_left_out_of_the_provider_comparison(self):
        self.line.estimated_unit_cost = Decimal("12")
        self.line.save(update_fields=["estimated_unit_cost"])
        sale = self._sale(self.company, self.product, "0588888888", Decimal("57"))
        Sale.objects.filter(pk=sale.pk).update(is_new_sim=True)
        rows = [
            _row(phone="0588888888", delta="-45", before="100", after="55", cost="45"),
        ]
        result = reconcile_sky_report(
            self.company,
            rows,
            period_from=date(2026, 8, 1),
            period_to=date(2026, 8, 31),
            min_amount_diff=Decimal("3"),
        )
        self.assertEqual(result.amount_mismatches, [])
        matched = next(r for r in result.matched if r.phone == "0588888888")
        self.assertEqual(matched.rd_net, Decimal("45"))
        self.assertEqual(matched.gap, Decimal("0"))

    def test_esim_sum_matches_rd_snapshot(self):
        self._sale(self.company, self.product, "0577777777", Decimal("50"))
        rows = [
            _row(phone="0577777777", delta="-45", before="100", after="55", cost="45"),
            _row(
                phone="0577777777",
                note="esim",
                delta="-5",
                before="55",
                after="50",
                cost="5",
                at="2026-08-10 14:00:00",
            ),
        ]
        result = reconcile_sky_report(
            self.company,
            rows,
            period_from=date(2026, 8, 1),
            period_to=date(2026, 8, 31),
            min_amount_diff=Decimal("3"),
        )
        self.assertEqual(result.amount_mismatches, [])
        self.assertTrue(any(r.phone == "0577777777" for r in result.matched))

    def test_other_supplier(self):
        self._sale(self.other, self.other_product, "0588888888", Decimal("45"))
        rows = [_row(phone="0588888888")]
        result = reconcile_sky_report(
            self.company,
            rows,
            period_from=date(2026, 8, 1),
            period_to=date(2026, 8, 31),
            min_amount_diff=Decimal("3"),
        )
        self.assertEqual(len(result.logged_other_supplier), 1)

    def test_gate_and_view(self):
        self.assertTrue(_supports_reconcile(self.company, "sky"))
        self.assertFalse(_supports_reconcile(self.other, "sky"))
        self.client.force_login(self.user)
        r = self.client.get(reverse("companies:sky_reconcile", args=[self.other.pk]))
        self.assertEqual(r.status_code, 302)
        r = self.client.get(reverse("companies:sky_reconcile", args=[self.company.pk]))
        self.assertEqual(r.status_code, 200)
        form = r.context["form"]
        self.assertIsNone(form["period_from"].value())
        self.assertIsNone(form["period_to"].value())

        with patch.object(
            SkyAdapter,
            "fetch_balance_rows",
            return_value=[_row(phone="0599999999")],
        ):
            r = self.client.post(
                reverse("companies:sky_reconcile", args=[self.company.pk]),
                {
                    "period_from": "2026-08-01",
                    "period_to": "2026-08-31",
                    "min_amount_diff": "3",
                },
            )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "0599999999")
        row = ProviderOperation.objects.get(service="reconcile")
        self.assertEqual((row.provider_key, row.outcome), ("sky", "success"))
        self.assertEqual((row.source, row.user_id), ("management", self.user.pk))

    def test_view_shows_fetch_error_for_provider_errors(self):
        self.client.force_login(self.user)
        with patch.object(
            SkyAdapter,
            "fetch_balance_rows",
            side_effect=TransientError("login_failed"),
        ):
            r = self.client.post(
                reverse("companies:sky_reconcile", args=[self.company.pk]),
                {
                    "period_from": "2026-08-01",
                    "period_to": "2026-08-31",
                    "min_amount_diff": "3",
                },
                follow=True,
            )
        row = ProviderOperation.objects.get(service="reconcile")
        self.assertEqual((row.outcome, row.error_code), ("failed", "login_failed"))
        self.assertEqual(row.message, "فشل المطابقة — خطأ تسجيل الدخول")
        self.assertEqual(row.user_id, self.user.pk)
        msgs = [str(m) for m in r.context["messages"]]
        self.assertEqual(
            msgs,
            [
                "Could not sign in to Sky. Check the connection settings on the "
                "company page."
            ],
        )
        self.assertNotContains(r, "login_failed")


class _FakeSkyAdapter(SkyAdapter):
    def __init__(self):
        self.login_calls = 0
        self.fetch_calls = []

    def login(self):
        self.login_calls += 1

    def fetch_balance_rows(self, date_from, date_to):
        self.fetch_calls.append((date_from, date_to))
        if self.login_calls == 0:
            raise AuthRequired("session not ready")
        return [_row(phone="0599999999")]


class FetchSkyRowsTests(TestCase):
    def setUp(self):
        get_redis().flushall()
        _save_sky_credentials()

    def test_logs_in_once_then_fetches(self):
        company = Company.objects.create(
            name="Sky Co",
            opening_balance=0,
            current_balance=0,
            provider_key="sky",
        )
        adapter = _FakeSkyAdapter()
        with patch("providers.service.get_adapter", return_value=adapter):
            result, template = service.reconcile(
                company,
                date(2026, 8, 1),
                date(2026, 8, 31),
                min_amount_diff=Decimal("3"),
            )
        self.assertEqual(result.not_recorded[0].phone, "0599999999")
        self.assertEqual(template, "companies/sky_reconcile.html")
        self.assertEqual(adapter.login_calls, 1)
        self.assertEqual(
            adapter.fetch_calls, [(date(2026, 8, 1), date(2026, 8, 31))] * 2
        )


class SkySupplierAliasTests(TestCase):
    """Provider-neutral names the shared reconcile templates read."""

    def test_row_and_result_expose_supplier_values(self):
        from providers.adapters.sky.reconcile import (
            SkyReconcilePhoneRow,
            SkyReconcileResult,
        )

        row = SkyReconcilePhoneRow(
            raw="0521234567",
            phone="0521234567",
            sky_net=Decimal("12.5"),
            sky_charges=Decimal("12.5"),
            sky_refunds=Decimal("0"),
            rd_net=Decimal("0"),
            gap=Decimal("12.5"),
            category="not_recorded",
            category_label="",
            lines=[],
        )
        self.assertEqual(row.supplier_net, Decimal("12.5"))
        result = SkyReconcileResult(
            period_from=None,
            period_to=None,
            sky_end_balance=Decimal("7.25"),
            rd_balance_end=Decimal("0"),
            not_recorded=[],
            split_settlements=[],
            amount_mismatches=[],
            matched=[],
            rd_only=[],
            logged_other_supplier=[],
            balance_anomalies=[],
            total_not_recorded=Decimal("0"),
            total_split_settlements=Decimal("0"),
            total_amount_mismatch=Decimal("0"),
            total_rd_only=Decimal("0"),
            total_logged_other_supplier=Decimal("0"),
            estimated_deficit=Decimal("0"),
            row_count=0,
            balance_gap=Decimal("0"),
            min_amount_diff=Decimal("3"),
        )
        self.assertEqual(result.supplier_end_balance, Decimal("7.25"))
        self.assertEqual(result.missing_count, 0)
        self.assertEqual(result.missing_total, Decimal("0"))
        result.total_not_recorded = Decimal("41.25")
        result.total_logged_other_supplier = Decimal("9.1")
        result.not_recorded = [row]
        result.logged_other_supplier = [row, row]
        self.assertEqual(result.missing_count, 3)
        self.assertEqual(result.missing_total, Decimal("50.35"))
        result.sky_end_balance = None
        self.assertIsNone(result.supplier_end_balance)

    def test_settlement_reason_matches_the_deficit_rule(self):
        from django.utils import translation

        from providers.adapters.sky.reconcile import explain_sky_row

        with translation.override("en"):
            text = explain_sky_row(
                category="settlement",
                sky_net=Decimal("5"),
                rd_net=Decimal("0"),
                gap=Decimal("5"),
            )
        self.assertIn("included in estimated deficit", text)
        self.assertNotIn("not included", text)
