"""Sales number check («فحص الرقم»): routing, gate, job submit/poll and card."""
from __future__ import annotations

import datetime as dt
import os
import re
import uuid
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserProfile
from companies.models import Company, Product, ProductLine
from phone_refresh.models import SystemSettings
from providers import jobs
from providers.adapters.sky.adapter import SkyAdapter
from providers.contracts import NumberInfo, PermanentError
from providers.crypto import encrypt
from providers.models import ProviderConnection, ProviderJob, ProviderOperation
from providers.redis_conn import get_redis
from providers.tasks import run_number_info_job
from sales import number_check
from sales.models import PaymentMethod, Sale
from sales.number_check import card_context, check_payload, last_sale_for_phone
from sales.services import create_sale

User = get_user_model()

PHONE = "0525737739"
TODAY = dt.date(2026, 10, 14)
TZ_PLUS_2 = dt.timezone(dt.timedelta(hours=2))
# Sky's own price: must never reach the browser. The rest of the fixture data
# is chosen so that "29" does not appear anywhere else in the card.
SKY_PRICE = Decimal("29")
ICCID = "8997201234567890123"

INFO = NumberInfo(
    network="سلكوم",
    is_esim=True,
    status="مفعل",
    plan_name="حملة رمضان 400 جيجا سلكوم",
    plan_price=SKY_PRICE,
    ends_at=dt.datetime(2026, 11, 13, 23, 30, tzinfo=TZ_PLUS_2),
    data_used_gb=Decimal("6.08"),
    data_total_gb=Decimal("400"),
    minutes_used=0,
    minutes_total=5000,
    sms_used=0,
    sms_total=5000,
    utilization_pct=Decimal("1.52"),
    iccid=ICCID,
)

NOT_AVAILABLE = "فحص الرقم غير متاح لهذا المزوّد"
EXPIRED = "انتهت صلاحية الطلب، أعد المحاولة."
STILL_RUNNING = "الطلب ما زال قيد المعالجة، حاول بعد دقيقة"
BUSY = "الخدمة مشغولة حالياً، حاول بعد قليل"


def _save_credentials(key="sky"):
    connection = ProviderConnection.for_key(key)
    connection.username = "user"
    connection.secret_encrypted = encrypt("pass")
    connection.save()
    return connection


def _profile(user, role):
    UserProfile.objects.update_or_create(
        user=user, defaults={"role": role, "is_active_profile": True}
    )


class _Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.emp = User.objects.create_user("nc_emp", password="x")
        _profile(cls.emp, UserProfile.Role.EMPLOYEE)
        cls.boss = User.objects.create_user("nc_boss", password="x")
        _profile(cls.boss, UserProfile.Role.MANAGEMENT)
        cls.company = Company.objects.create(
            name="Sky Co", provider_key="sky", opening_balance=1000, current_balance=1000
        )
        line = ProductLine.objects.create(company=cls.company, name="سلكوم")
        cls.product = Product.objects.create(
            line=line,
            variant_label="رمضان",
            cost_price=Decimal("5"),
            default_sell_price=Decimal("60"),
        )
        cls.pm = PaymentMethod.objects.create(name="Cash")

    def setUp(self):
        get_redis().flushall()
        ProviderConnection.objects.all().delete()
        _save_credentials("sky")
        self.client.force_login(self.emp)
        patcher = patch.object(SkyAdapter, "number_info", return_value=INFO)
        self.adapter_call = patcher.start()
        self.addCleanup(patcher.stop)

    def make_sale(self, phone=PHONE, company=None, product=None, when=None):
        sale = create_sale(
            company=company or self.company,
            product=product or self.product,
            reference_number=phone,
            payer_name="Ali",
            payment_method=self.pm,
            sell_price_actual=Decimal("60"),
            notes="",
            user=self.emp,
        )
        if when is None:
            when = dt.datetime(2026, 10, 14, 10, 0, tzinfo=TZ_PLUS_2)
        Sale.objects.filter(pk=sale.pk).update(created_at=when)
        sale.refresh_from_db()
        return sale

    def post(self, phone=PHONE, client=None):
        return (client or self.client).post(
            reverse("sales:employee_check_phone"), {"phone": phone}
        )

    def status(self, job_id, client=None):
        return (client or self.client).get(
            reverse("sales:employee_check_status", args=[job_id])
        )

    def run_check(self, phone=PHONE, client=None):
        """POST then poll with the job executed on commit; returns the status response."""
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.post(phone, client)
        self.assertEqual(resp.status_code, 202, resp.content)
        return resp, self.status(resp.json()["job"], client)

    def rows(self):
        return ProviderOperation.objects.filter(service="number_info")


class SubmitRoutingTests(_Base):
    def test_invalid_phone_is_rejected_without_row_or_job(self):
        resp = self.post("12345")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "error", "message": "رقم غير صحيح"})
        self.assertFalse(self.rows().exists())
        self.assertFalse(ProviderJob.objects.exists())

    def test_phone_is_stripped_before_validation(self):
        self.make_sale()
        _, status = self.run_check(f"  {PHONE} ")
        self.assertEqual(status.json()["status"], "ok")

    def test_no_sale_is_rejected_without_row_or_job(self):
        resp = self.post()
        self.assertEqual(
            resp.json(),
            {"status": "error", "message": "لا توجد عملية بيع سابقة لهذا الرقم"},
        )
        self.assertFalse(self.rows().exists())
        self.assertFalse(ProviderJob.objects.exists())
        self.adapter_call.assert_not_called()

    def test_provider_without_capability_is_unavailable_and_silent(self):
        aloha = Company.objects.create(
            name="Aloha Co", provider_key="aloha", opening_balance=1, current_balance=1
        )
        line = ProductLine.objects.create(company=aloha, name="A")
        product = Product.objects.create(
            line=line, cost_price=Decimal("1"), default_sell_price=Decimal("2")
        )
        self.make_sale(company=aloha, product=product)
        resp = self.post()
        self.assertEqual(
            resp.json(), {"status": "error", "message": NOT_AVAILABLE}
        )
        self.assertFalse(ProviderOperation.objects.exists())
        self.assertFalse(ProviderJob.objects.exists())

    def test_blank_provider_key_is_unavailable_and_silent(self):
        plain = Company.objects.create(
            name="Plain Co", provider_key="", opening_balance=1, current_balance=1
        )
        line = ProductLine.objects.create(company=plain, name="P")
        product = Product.objects.create(
            line=line, cost_price=Decimal("1"), default_sell_price=Decimal("2")
        )
        self.make_sale(company=plain, product=product)
        resp = self.post()
        self.assertEqual(resp.json()["message"], NOT_AVAILABLE)
        self.assertFalse(ProviderOperation.objects.exists())
        self.assertFalse(ProviderJob.objects.exists())

    def test_connection_off_records_one_service_off_row(self):
        sale = self.make_sale()
        connection = ProviderConnection.for_key("sky")
        connection.is_enabled = False
        connection.save()
        resp = self.post()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {"status": "error", "message": "فشل فحص الرقم — الخدمة موقوفة"},
        )
        row = self.rows().get()
        self.assertEqual(row.provider_key, "sky")
        self.assertEqual(row.error_code, "service_off")
        self.assertEqual(row.outcome, "failed")
        self.assertEqual(row.phone, PHONE)
        self.assertEqual(row.source, "employee")
        self.assertEqual(row.user_id, self.emp.pk)
        self.assertEqual(row.sale_id, sale.pk)
        self.assertFalse(ProviderJob.objects.exists())
        self.adapter_call.assert_not_called()

    def test_missing_credentials_record_one_not_configured_row(self):
        sale = self.make_sale()
        ProviderConnection.objects.all().delete()
        ProviderConnection.for_key("sky")  # a row without credentials is no account
        with patch.dict(
            os.environ, {"SKY_SALES_USER": "", "SKY_SALES_PASSWORD": ""}
        ):
            resp = self.post()
        self.assertEqual(
            resp.json(),
            {"status": "error", "message": "فشل فحص الرقم — بيانات الحساب ناقصة"},
        )
        row = self.rows().get()
        self.assertEqual(row.error_code, "not_configured")
        self.assertEqual(row.sale_id, sale.pk)
        self.assertFalse(ProviderJob.objects.exists())

    def test_management_gate_row_source(self):
        self.make_sale()
        connection = ProviderConnection.for_key("sky")
        connection.is_enabled = False
        connection.save()
        self.client.force_login(self.boss)
        self.post()
        row = self.rows().get()
        self.assertEqual(row.source, "management")
        self.assertEqual(row.user_id, self.boss.pk)

    def test_last_sale_is_the_newest_for_the_number(self):
        old = self.make_sale(when=dt.datetime(2026, 1, 1, 10, 0, tzinfo=TZ_PLUS_2))
        new = self.make_sale(when=dt.datetime(2026, 10, 1, 10, 0, tzinfo=TZ_PLUS_2))
        self.make_sale(phone="0525737700")
        found = last_sale_for_phone(PHONE)
        self.assertEqual(found.pk, new.pk)
        self.assertNotEqual(found.pk, old.pk)
        self.assertIsNone(last_sale_for_phone("0525730000"))


class HappyPathTests(_Base):
    def test_post_then_status_renders_the_card(self):
        sale = self.make_sale()
        resp, status = self.run_check()
        self.assertEqual(resp.json()["status"], "processing")
        self.assertEqual(status.status_code, 200)
        body = status.json()
        self.assertEqual(set(body), {"status", "html"})
        self.assertEqual(body["status"], "ok")
        html = body["html"]
        for expected in (
            "سلكوم",
            "شريحة إلكترونية",
            "مفعل",
            "حملة رمضان 400 جيجا سلكوم",
            "13/11/2026",
            "متبقي 30 يوم",
            "6.08 من 400 جيجا",
            "1.52%",
            ICCID,
            "60 ₪",
            self.product.display_name,
            "14/10/2026",
        ):
            self.assertIn(expected, html)
        # Exactly one provider operation, with the sale and the user.
        row = self.rows().get()
        self.assertEqual(row.outcome, "success")
        self.assertEqual(row.source, "employee")
        self.assertEqual(row.user_id, self.emp.pk)
        self.assertEqual(row.sale_id, sale.pk)
        self.adapter_call.assert_called_once_with(PHONE)

    def test_management_check_is_recorded_as_management(self):
        self.make_sale()
        self.client.force_login(self.boss)
        _, status = self.run_check()
        self.assertEqual(status.json()["status"], "ok")
        self.assertEqual(self.rows().get().source, "management")
        self.assertEqual(ProviderJob.objects.get().source, "management")

    def test_job_payload_holds_only_user_and_sale(self):
        sale = self.make_sale()
        self.run_check()
        job = ProviderJob.objects.get()
        self.assertEqual(job.kind, ProviderJob.KIND_NUMBER_INFO)
        self.assertEqual(job.provider_key, "sky")
        self.assertEqual(job.phone, PHONE)
        self.assertEqual(job.payload, {"user_id": self.emp.pk, "sale_id": sale.pk})

    def test_sky_price_never_reaches_the_browser(self):
        self.make_sale()
        post, status = self.run_check()
        for resp in (post, status):
            self.assertNotIn("plan_price", resp.content.decode())
            self.assertNotIn("error_code", resp.content.decode())
        # The raw body is ``\uXXXX``-escaped Arabic (hex digits can contain "29"),
        # so check the decoded card text: Sky's 29 appears nowhere in it, and our
        # own price shows as 60.
        html = status.json()["html"]
        self.assertNotIn("29", html)
        self.assertNotIn("plan_price", html)
        self.assertNotIn("₪29", html)
        self.assertNotIn("29 ₪", html)
        self.assertIn("60 ₪", html)
        # The stored job result keeps it (phase 3), the response does not.
        self.assertEqual(ProviderJob.objects.get().result["info"]["plan_price"], "29")

    def test_deleted_sale_shows_dash_for_price(self):
        sale = self.make_sale()
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.post()
        Sale.objects.filter(pk=sale.pk).delete()
        html = self.status(resp.json()["job"]).json()["html"]
        self.assertIn("—", html)
        self.assertNotIn("60 ₪", html)

    def test_second_post_joins_the_inflight_job(self):
        self.make_sale()
        with self.captureOnCommitCallbacks(execute=True):
            first = self.post()
            second = self.post()
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first.json()["job"], second.json()["job"])
        self.assertEqual(ProviderJob.objects.count(), 1)
        self.adapter_call.assert_called_once()
        self.assertEqual(self.rows().count(), 1)

    def test_not_found_failure_is_returned_by_message_only(self):
        self.make_sale()
        self.adapter_call.side_effect = PermanentError("not_found")
        _, status = self.run_check()
        self.assertEqual(
            status.json(),
            {"status": "error", "message": "فشل فحص الرقم — الرقم غير موجود لدى المزوّد"},
        )
        self.assertEqual(self.rows().get().error_code, "not_found")


class EnqueueFailureTests(_Base):
    def test_enqueue_failure_answers_busy_and_fails_the_job_without_a_row(self):
        self.make_sale()
        # The test case wraps everything in a transaction, so run the on-commit
        # hook immediately like a request outside a transaction would.
        with patch.object(
            run_number_info_job, "delay", side_effect=RuntimeError("broker down")
        ), patch(
            "sales.number_check.transaction.on_commit", side_effect=lambda fn: fn()
        ):
            resp = self.post()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "error", "message": BUSY})
        job = ProviderJob.objects.get()
        self.assertEqual(job.status, ProviderJob.STATUS_FAILED)
        self.assertEqual(job.result, {"status": "error", "message": BUSY})
        self.assertFalse(self.rows().exists())
        self.adapter_call.assert_not_called()

    def test_enqueue_failure_after_commit_is_what_the_poll_sees(self):
        self.make_sale()
        with patch.object(
            run_number_info_job, "delay", side_effect=RuntimeError("broker down")
        ), self.captureOnCommitCallbacks(execute=True):
            resp = self.post()
        self.assertEqual(resp.status_code, 202)
        status = self.status(resp.json()["job"])
        self.assertEqual(status.json(), {"status": "error", "message": BUSY})
        self.assertFalse(self.rows().exists())
        self.adapter_call.assert_not_called()


class PollTests(_Base):
    def _job(self, **kwargs):
        return jobs.create_job(
            kind=ProviderJob.KIND_NUMBER_INFO,
            provider_key="sky",
            phone=PHONE,
            source="employee",
            payload={"user_id": self.emp.pk, "sale_id": None},
            **kwargs,
        )

    def test_failed_job_returns_its_message_without_error_code(self):
        job = self._job()
        jobs.mark_failed(
            job,
            {"status": "error", "message": "فشل فحص الرقم — تعذّر الاتصال بالمزوّد", "error_code": "connection"},
        )
        resp = self.status(job.id)
        self.assertEqual(
            resp.json(),
            {"status": "error", "message": "فشل فحص الرقم — تعذّر الاتصال بالمزوّد"},
        )

    def test_queued_job_is_processing(self):
        job = self._job()
        self.assertEqual(self.status(job.id).json(), {"status": "processing"})

    def test_running_job_is_processing(self):
        job = self._job()
        jobs.mark_running(job)
        self.assertEqual(self.status(job.id).json(), {"status": "processing"})

    def test_old_queued_job_gets_the_final_message(self):
        job = self._job()
        wait = SystemSettings.get().refresh_wait_seconds
        ProviderJob.objects.filter(pk=job.pk).update(
            created_at=timezone.now() - dt.timedelta(seconds=wait + 5)
        )
        self.assertEqual(
            self.status(job.id).json(),
            {"status": "error", "message": STILL_RUNNING, "final": True},
        )

    def test_unknown_uuid_is_expired(self):
        self.assertEqual(
            self.status(uuid.uuid4()).json(), {"status": "error", "message": EXPIRED}
        )

    def test_bad_job_id_is_expired_in_the_helper(self):
        self.assertEqual(check_payload("not-a-uuid"), {"status": "error", "message": EXPIRED})

    def test_other_kind_of_job_is_expired(self):
        job = jobs.create_job(
            kind=ProviderJob.KIND_REFRESH,
            provider_key="sky",
            phone=PHONE,
            source="employee",
        )
        self.assertEqual(self.status(job.id).json(), {"status": "error", "message": EXPIRED})

    def test_done_job_without_result_is_expired(self):
        job = self._job()
        jobs.mark_done(job, {})
        self.assertEqual(self.status(job.id).json(), {"status": "error", "message": EXPIRED})

    def test_refresh_status_endpoint_never_returns_a_number_check_result(self):
        job = self._job()
        jobs.mark_done(job, {"status": "ok", "info": INFO.to_dict()})
        resp = self.client.get(reverse("sales:employee_refresh_status", args=[job.id]))
        body = resp.json()
        self.assertEqual(body["status"], "error")
        self.assertNotIn("info", body)
        self.assertNotIn(b"plan_price", resp.content)


class AccessTests(_Base):
    def test_employee_without_the_setting_gets_403(self):
        self.make_sale()
        disabled = type("S", (), {"sales_show_refresh_phone": False})()
        with patch("sales.views.employee.AppSettings.load", return_value=disabled):
            post = self.post()
            status = self.status(uuid.uuid4())
        self.assertEqual(post.status_code, 403)
        self.assertEqual(status.status_code, 403)
        self.assertEqual(post.json()["status"], "error")
        self.assertFalse(ProviderJob.objects.exists())

    def test_anonymous_is_redirected_to_login(self):
        self.client.logout()
        post = self.post()
        status = self.status(uuid.uuid4())
        self.assertEqual(post.status_code, 302)
        self.assertEqual(status.status_code, 302)
        self.assertIn("login", post["Location"])
        self.assertFalse(ProviderJob.objects.exists())

    def test_post_only_and_get_only(self):
        self.assertEqual(
            self.client.get(reverse("sales:employee_check_phone")).status_code, 405
        )
        self.assertEqual(
            self.client.post(
                reverse("sales:employee_check_status", args=[uuid.uuid4()])
            ).status_code,
            405,
        )

    def test_status_is_not_cached(self):
        resp = self.status(uuid.uuid4())
        self.assertIn("no-cache", resp["Cache-Control"])


class CardContextTests(_Base):
    def sale(self, price="60", created=dt.datetime(2026, 10, 14, 9, 0, tzinfo=TZ_PLUS_2)):
        self.product.default_sell_price = Decimal(price)
        return Sale(product=self.product, company=self.company, created_at=created)

    def ctx(self, info=INFO, sale=None, today=TODAY):
        return card_context(info, sale, today)

    def test_full_row_matches_the_spec_table(self):
        ctx = self.ctx(sale=self.sale())
        self.assertEqual(ctx["network"], "سلكوم")
        self.assertEqual(ctx["sim_type"], "شريحة إلكترونية")
        self.assertEqual(ctx["status"], "مفعل")
        self.assertEqual(ctx["plan_name"], "حملة رمضان 400 جيجا سلكوم")
        self.assertEqual(ctx["expiry"], "13/11/2026 — متبقي 30 يوم")
        self.assertEqual(
            ctx["usage"],
            "إنترنت 6.08 من 400 جيجا · دقائق 0 من 5000 · رسائل 0 من 5000 · نسبة الاستخدام 1.52%",
        )
        self.assertEqual(ctx["iccid"], ICCID)
        self.assertEqual(
            ctx["sale_price"], "60 ₪ — سلكوم — رمضان (آخر بيعة 14/10/2026)"
        )

    def test_physical_sim(self):
        self.assertEqual(self.ctx(NumberInfo(is_esim=False))["sim_type"], "شريحة عادية")

    def test_price_formatting_has_no_trailing_zeros(self):
        self.assertTrue(self.ctx(sale=self.sale("60.00"))["sale_price"].startswith("60 ₪ —"))
        self.assertTrue(self.ctx(sale=self.sale("60.50"))["sale_price"].startswith("60.5 ₪ —"))
        self.assertTrue(self.ctx(sale=self.sale("7.25"))["sale_price"].startswith("7.25 ₪ —"))

    def test_none_fields_show_a_dash(self):
        ctx = self.ctx(NumberInfo())
        for key in ("network", "status", "plan_name", "expiry", "usage", "iccid", "sale_price"):
            self.assertEqual(ctx[key], "—", key)

    def test_partial_usage_shows_dashes_only_where_missing(self):
        info = NumberInfo(data_used_gb=Decimal("1.50"), data_total_gb=None, minutes_total=100)
        usage = self.ctx(info)["usage"]
        self.assertIn("إنترنت 1.5 جيجا", usage)
        self.assertIn("دقائق — من 100", usage)
        self.assertIn("رسائل —", usage)
        self.assertIn("نسبة الاستخدام —", usage)

    def test_expiry_today(self):
        ends = dt.datetime(2026, 10, 14, 12, 0, tzinfo=TZ_PLUS_2)
        ctx = self.ctx(NumberInfo(ends_at=ends))
        self.assertEqual(ctx["expiry"], "14/10/2026 — تنتهي اليوم")

    def test_expiry_past(self):
        ends = dt.datetime(2026, 10, 1, 12, 0, tzinfo=TZ_PLUS_2)
        self.assertEqual(self.ctx(NumberInfo(ends_at=ends))["expiry"], "01/10/2026 — منتهية")

    def test_expiry_uses_the_local_date_of_the_end_time(self):
        # 22:30 UTC on the 13th is already the 14th in Asia/Jerusalem (UTC+3 in
        # summer / +2 in winter): the local date decides, not the offset in the data.
        ends = dt.datetime(2026, 11, 13, 22, 30, tzinfo=dt.timezone.utc)
        ctx = self.ctx(NumberInfo(ends_at=ends))
        self.assertEqual(ctx["expiry"], "14/11/2026 — متبقي 31 يوم")

    def test_no_sale_shows_a_dash(self):
        self.assertEqual(self.ctx(sale=None)["sale_price"], "—")

    def test_sale_date_is_the_local_date(self):
        created = dt.datetime(2026, 10, 13, 22, 30, tzinfo=dt.timezone.utc)
        ctx = self.ctx(sale=self.sale(created=created))
        self.assertIn("(آخر بيعة 14/10/2026)", ctx["sale_price"])

    def test_provider_strings_are_escaped_in_the_card(self):
        info = NumberInfo(network="<b>x</b>", plan_name="<script>alert(1)</script>")
        html = render_to_string(
            "sales/partials/number_check_card.html", self.ctx(info, self.sale())
        )
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", html)
        self.assertNotIn("<b>x</b>", html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_card_lists_every_field_as_a_labelled_row_in_spec_order(self):
        html = render_to_string(
            "sales/partials/number_check_card.html", self.ctx(INFO, self.sale())
        )
        labels = [
            "الشركة",
            "نوع الشريحة",
            "الحالة",
            "اسم الحزمة",
            "تاريخ الانتهاء",
            "الاستهلاك",
            "رقم الشريحة",
            "سعر البيع",
        ]
        positions = []
        for label in labels:
            match = re.search(r"<dt[^>]*>\s*%s\s*</dt>" % label, html)
            self.assertIsNotNone(match, label)
            positions.append(match.start())
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(html.count("<dt"), len(labels))
        self.assertEqual(html.count("<dd"), len(labels))
        # Company and SIM type are values of their own rows now (no unlabelled header).
        self.assertRegex(html, r"<dd[^>]*>\s*سلكوم\s*</dd>")
        self.assertRegex(html, r"<dd[^>]*>\s*شريحة إلكترونية\s*</dd>")
        self.assertNotIn("rd-badge", html)

    def test_card_has_no_dead_hook_class_or_inline_style(self):
        html = render_to_string(
            "sales/partials/number_check_card.html", self.ctx(INFO, self.sale())
        )
        self.assertNotIn("rd-number-card", html)
        self.assertNotIn("style=", html)

    def test_card_never_mentions_the_provider_price(self):
        html = render_to_string(
            "sales/partials/number_check_card.html", self.ctx(INFO, self.sale())
        )
        self.assertNotIn("29", html)
        self.assertNotIn("plan_price", html)

    def test_helper_module_does_not_leak_info_fields(self):
        self.assertNotIn("plan_price", self.ctx(INFO, self.sale()))
        self.assertTrue(callable(number_check.submit_number_check))
