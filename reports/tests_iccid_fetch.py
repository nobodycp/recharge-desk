"""Fetch ICCID from the provider into بيان الأرقام ``sim_identifier``."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserProfile
from companies.models import Company, Product, ProductLine
from providers.contracts import NumberInfo
from providers import operations, service
from providers.redis_conn import get_redis
from reports.iccid_fetch import (
    fill_sim_identifiers_from_provider,
    job_status,
    start_iccid_fetch_job,
)
from reports.models import PhoneLineTracking
from sales.models import PaymentMethod, Sale
from sales.services import create_sale, mark_sale_paid

User = get_user_model()


class FillSimIdentifiersTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("mgr_iccid", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        cls.company = Company.objects.create(
            name="IccidCo",
            provider_key="fake",
            opening_balance=Decimal("1000"),
            current_balance=Decimal("1000"),
        )
        line = ProductLine.objects.create(company=cls.company, name="L")
        cls.product = Product.objects.create(
            line=line, cost_price=Decimal("5"), default_sell_price=Decimal("20")
        )
        cls.cash = PaymentMethod.objects.create(name="Cash")

    def setUp(self):
        get_redis().flushall()

    def _sale(self, ref: str) -> Sale:
        sale = create_sale(
            company=self.company,
            product=self.product,
            reference_number=ref,
            payer_name="P",
            payment_method=self.cash,
            sell_price_actual=Decimal("20"),
            notes="",
            user=self.user,
        )
        mark_sale_paid(sale=sale, user=self.user)
        return sale

    def test_fills_empty_iccid_and_skips_existing(self):
        self._sale("055ICCID001")
        self._sale("055ICCID002")
        PhoneLineTracking.objects.create(
            reference_key="055iccid002",
            sim_identifier="ALREADY-THERE",
            last_display_reference="055ICCID002",
        )

        ok = NumberInfo(iccid="8997000000000000001")
        with patch(
            "reports.iccid_fetch.service_state",
            return_value=type("S", (), {"code": "active"})(),
        ), patch(
            "reports.iccid_fetch.number_info",
            return_value=service.NumberInfoResult(
                info=ok, classified=operations.success("number_info")
            ),
        ) as mocked:
            counts = fill_sim_identifiers_from_provider(
                ["055ICCID001", "055ICCID002"], user=self.user
            )

        self.assertEqual(counts, {"filled": 1, "skipped": 1, "failed": 0})
        self.assertEqual(mocked.call_count, 1)
        meta = PhoneLineTracking.objects.get(reference_key="055iccid001")
        self.assertEqual(meta.sim_identifier, "8997000000000000001")

    def test_chunked_job_fills_and_reports_progress(self):
        self._sale("055ICCIDJ01")
        self._sale("055ICCIDJ02")
        ok = NumberInfo(iccid="8997000000000000099")
        with patch(
            "reports.iccid_fetch.service_state",
            return_value=type("S", (), {"code": "active"})(),
        ), patch(
            "reports.iccid_fetch.number_info",
            return_value=service.NumberInfoResult(
                info=ok, classified=operations.success("number_info")
            ),
        ), override_settings(CELERY_TASK_ALWAYS_EAGER=True):
            job_id = start_iccid_fetch_job(
                ["055ICCIDJ01", "055ICCIDJ02"], user_id=self.user.pk
            )
        status = job_status(job_id)
        self.assertIsNotNone(status)
        self.assertEqual(status["status"], "done")
        self.assertEqual(status["total"], 2)
        self.assertEqual(status["done"], 2)
        self.assertEqual(status["filled"], 2)
        self.assertFalse(status["running"])
        self.assertEqual(
            PhoneLineTracking.objects.get(reference_key="055iccidj01").sim_identifier,
            "8997000000000000099",
        )


class BulkFetchIccidViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("mgr_iccid_v", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        cls.company = Company.objects.create(
            name="IccidViewCo",
            provider_key="fake",
            opening_balance=Decimal("1000"),
            current_balance=Decimal("1000"),
        )
        line = ProductLine.objects.create(company=cls.company, name="L")
        cls.product = Product.objects.create(
            line=line, cost_price=Decimal("5"), default_sell_price=Decimal("20")
        )
        cls.cash = PaymentMethod.objects.create(name="Cash")

    def setUp(self):
        get_redis().flushall()
        self.client = Client()
        self.client.force_login(self.user)

    def test_bulk_starts_background_job_without_blocking_fill(self):
        sale = create_sale(
            company=self.company,
            product=self.product,
            reference_number="055ICCIDV01",
            payer_name="P",
            payment_method=self.cash,
            sell_price_actual=Decimal("20"),
            notes="",
            user=self.user,
        )
        mark_sale_paid(sale=sale, user=self.user)
        Sale.objects.filter(pk=sale.pk).update(
            created_at=timezone.now() - timedelta(days=5),
            duration_days=30,
        )

        page = self.client.get(reverse("reports:phone_lines_report"))
        self.assertContains(page, 'value="fetch_iccid"')
        self.assertContains(page, "data-iccid-progress")

        with patch(
            "reports.views_stale_phones.start_iccid_fetch_job",
            return_value="jobabc",
        ) as start:
            resp = self.client.post(
                reverse("reports:phone_lines_bulk"),
                {"action": "fetch_iccid", "refs": ["055ICCIDV01"]},
                HTTP_HX_REQUEST="true",
            )
        self.assertEqual(resp.status_code, 200)
        start.assert_called_once()
        self.assertEqual(start.call_args.args[0], ["055iccidv01"])
        self.assertIn("iccidFetchStarted", resp["HX-Trigger"])
        self.assertIn("background", resp["HX-Trigger"])

    def test_status_endpoint_returns_progress_for_owner(self):
        with patch("reports.tasks.process_iccid_fetch_chunk.delay"):
            job_id = start_iccid_fetch_job(["055x"], user_id=self.user.pk)
        from reports.iccid_fetch import run_iccid_fetch_chunk

        with patch(
            "reports.iccid_fetch.fill_one_sim_identifier", return_value="failed"
        ):
            while run_iccid_fetch_chunk(job_id):
                pass
        resp = self.client.get(
            reverse("reports:phone_lines_iccid_status"), {"job": job_id}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["job_id"], job_id)
        self.assertEqual(data["status"], "done")
        self.assertEqual(data["failed"], 1)
