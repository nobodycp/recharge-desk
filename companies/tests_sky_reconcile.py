from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserProfile
from companies.models import Company, Product, ProductLine
from companies.sky_reconcile import (
    company_supports_sky_reconcile,
    parse_sky_balance_rows,
    reconcile_sky_report,
)
from sales.models import PaymentMethod, Sale

User = get_user_model()


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

    def test_settlement_pair_excluded_from_deficit(self):
        company = Company.objects.create(
            name="Sky",
            opening_balance=0,
            current_balance=0,
            phone_refresh_provider="sky",
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
        self.assertEqual(result.estimated_deficit, Decimal("0"))


class SkyReconcileMatchTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Sky Co",
            opening_balance=1000,
            current_balance=800,
            phone_refresh_provider="sky",
        )
        self.other = Company.objects.create(
            name="Other",
            opening_balance=0,
            current_balance=0,
            phone_refresh_provider="layan",
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
        self.assertTrue(company_supports_sky_reconcile(self.company))
        self.assertFalse(company_supports_sky_reconcile(self.other))
        self.client.force_login(self.user)
        r = self.client.get(reverse("companies:sky_reconcile", args=[self.other.pk]))
        self.assertEqual(r.status_code, 302)
        r = self.client.get(reverse("companies:sky_reconcile", args=[self.company.pk]))
        self.assertEqual(r.status_code, 200)

        with patch(
            "companies.views.fetch_sky_rows_for_reconcile",
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
