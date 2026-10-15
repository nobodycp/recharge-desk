"""Sales number check («فحص الرقم»): routing, gate, job submit/poll and card."""
from __future__ import annotations

import datetime as dt
import json
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
PHONE_DISPLAY = "052-573-7739"
TODAY = dt.date(2026, 10, 14)
TZ_PLUS_2 = dt.timezone(dt.timedelta(hours=2))
# "Now" for every check in this module: the card counts days from today, so the
# tests must not depend on the real date (nor on the minute, which could print "29").
NOW = dt.datetime(2026, 10, 14, 12, 5, tzinfo=TZ_PLUS_2)
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


SHARE_RE = re.compile(
    r'<script id="rd-ncheck-share-data" type="application/json">(.*?)</script>', re.S
)


def _share_json(html):
    """The decoded payload of the card's ``json_script`` (what the image is drawn from)."""
    match = SHARE_RE.search(html)
    assert match, "card has no share data"
    return json.loads(match.group(1))


def _visible(html):
    """The card markup without the (``\\uXXXX``-escaped) share payload."""
    return SHARE_RE.sub("", html)


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
        clock = patch.object(number_check, "_now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

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
            '<span class="rd-ncheck__big-num">30</span>',
            PHONE_DISPLAY,
            "إنترنت: 6.08 من 400 جيجا",
            "1.5%",
            ICCID,
            "<bdi>60</bdi>",
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

    def test_check_payload_passes_the_phone_and_branding_to_the_card(self):
        from django.core.cache import cache
        from core.models import SiteBranding

        SiteBranding.objects.update_or_create(pk=1, defaults={"site_name": "Acme Sim"})
        cache.clear()
        self.addCleanup(cache.clear)
        self.make_sale()
        _, status = self.run_check()
        html = status.json()["html"]
        share = _share_json(html)
        self.assertEqual(share["phone_display"], PHONE_DISPLAY)
        self.assertRegex(share["checked_at"], r"^14/10/2026 · \d\d:05$")
        self.assertIn('data-site-name="Acme Sim"', html)
        self.assertIn('data-site-logo=""', html)

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
        self.assertNotIn("29", _visible(html))
        self.assertNotIn("29", json.dumps(_share_json(html), ensure_ascii=False))
        self.assertNotIn("plan_price", html)
        self.assertNotIn("₪29", html)
        self.assertNotIn("29 ₪", html)
        self.assertIn("<bdi>60</bdi>", html)
        # The stored job result keeps it (phase 3), the response does not.
        self.assertEqual(ProviderJob.objects.get().result["info"]["plan_price"], "29")

    def test_deleted_sale_shows_dash_for_price(self):
        sale = self.make_sale()
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.post()
        Sale.objects.filter(pk=sale.pk).delete()
        html = self.status(resp.json()["job"]).json()["html"]
        self.assertIn("لا توجد بيعة سابقة لهذا الرقم", html)
        self.assertNotIn("<bdi>60</bdi>", html)

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
    def test_page_lives_at_check_number_and_the_old_path_redirects(self):
        url = reverse("sales:employee_refresh_number")
        self.assertTrue(url.endswith("/employee/sales/check-number/"), url)
        old = url.replace("check-number", "refresh-number")
        resp = self.client.get(old + "?x=1")
        self.assertRedirects(resp, url + "?x=1", status_code=302, fetch_redirect_response=False)

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

    def ctx(self, info=INFO, sale=None, today=TODAY, phone=PHONE, now=NOW):
        return card_context(info, sale, today, phone=phone, now=now)

    def card(self, info=INFO, sale="default", **kwargs):
        sale = self.sale() if sale == "default" else sale
        return render_to_string(
            "sales/partials/number_check_card.html", self.ctx(info, sale, **kwargs)
        )

    def line_with_icon(self, name, icon="icons/product_lines/x.webp", company=None):
        # Created first and given the icon by update: saving an ImageField would
        # try to optimise a real file.
        company = company or Company.objects.create(
            name=f"Co {name}", opening_balance=1, current_balance=1
        )
        line = ProductLine.objects.create(company=company, name=name)
        ProductLine.objects.filter(pk=line.pk).update(icon=icon)
        line.refresh_from_db()
        return line

    def test_full_row_matches_the_spec(self):
        ctx = self.ctx(sale=self.sale())
        self.assertEqual(ctx["network"], "سلكوم")
        self.assertEqual(ctx["network_tone"], "cellcom")
        self.assertEqual(ctx["sim_type"], "شريحة إلكترونية")
        self.assertEqual(ctx["status"], "مفعل")
        self.assertEqual(ctx["status_tone"], "ok")
        self.assertEqual(ctx["plan_name"], "حملة رمضان 400 جيجا سلكوم")
        self.assertEqual(
            ctx["expiry"],
            {"date": "13/11/2026", "days": "30", "word": "يوم", "state": "", "tone": "ok"},
        )
        self.assertEqual(ctx["utilization"], "1.5")
        self.assertTrue(ctx["has_usage"])
        self.assertEqual(ctx["iccid"], ICCID)
        self.assertEqual(ctx["iccid_groups"], "8997 2012 3456 7890 123")
        self.assertEqual(
            ctx["sale"], {"price": "60", "product": "سلكوم — رمضان", "date": "14/10/2026"}
        )
        self.assertEqual(ctx["phone"], PHONE)
        self.assertEqual(ctx["phone_display"], PHONE_DISPLAY)

    def test_gauges_carry_used_total_percent_and_remaining(self):
        data, minutes, sms = self.ctx()["usage"]
        self.assertEqual(
            data,
            {
                "key": "data", "label": "إنترنت", "unit": "جيجا",
                "used": "6.08", "total": "400", "pct": "1.5", "offset": "98.48",
                "remaining": "393.92", "tone": "ok", "included": True,
            },
        )
        self.assertEqual((minutes["used"], minutes["total"], minutes["pct"]), ("0", "5000", "0"))
        self.assertEqual(minutes["offset"], "")
        self.assertEqual(minutes["remaining"], "5000")
        self.assertEqual(sms["unit"], "رسالة")

    def test_gauge_tone_follows_how_much_is_used(self):
        def tone(used):
            return self.ctx(NumberInfo(minutes_used=used, minutes_total=100))["usage"][1]["tone"]

        self.assertEqual(tone(69), "ok")
        self.assertEqual(tone(70), "warn")
        self.assertEqual(tone(90), "danger")

    def test_gauge_is_clamped_when_usage_exceeds_the_allowance(self):
        gauge = self.ctx(NumberInfo(sms_used=150, sms_total=100))["usage"][2]
        self.assertEqual((gauge["pct"], gauge["offset"], gauge["remaining"]), ("100", "0.00", "0"))

    def test_zero_allowance_is_not_included(self):
        gauge = self.ctx(NumberInfo(sms_used=0, sms_total=0))["usage"][2]
        self.assertFalse(gauge["included"])
        self.assertEqual(gauge["pct"], "")
        self.assertIn("غير مشمولة بالحزمة", self.card(NumberInfo(sms_used=0, sms_total=0)))

    def test_used_without_total_has_no_percent(self):
        gauge = self.ctx(NumberInfo(data_used_gb=Decimal("1.50")))["usage"][0]
        self.assertEqual((gauge["used"], gauge["total"], gauge["pct"]), ("1.5", "", ""))
        self.assertTrue(gauge["included"])

    def test_physical_sim(self):
        self.assertEqual(self.ctx(NumberInfo(is_esim=False))["sim_type"], "شريحة عادية")

    def test_status_tones(self):
        self.assertEqual(self.ctx(NumberInfo(status="مفعل"))["status_tone"], "ok")
        self.assertEqual(self.ctx(NumberInfo(status="غير مفعل"))["status_tone"], "warn")
        self.assertEqual(self.ctx(NumberInfo(status="موقوف"))["status_tone"], "warn")
        self.assertEqual(self.ctx(NumberInfo(status="شيء آخر"))["status_tone"], "muted")
        self.assertEqual(self.ctx(NumberInfo())["status_tone"], "muted")

    def test_network_tones(self):
        for network, tone in (
            ("سلكوم", "cellcom"),
            ("بليفون", "pelephone"),
            ("Partner", "partner"),
            ("جوال", "jawwal"),
            ("جولان تيليكوم", "golan"),
            ("مزود جديد", "default"),
        ):
            self.assertEqual(self.ctx(NumberInfo(network=network))["network_tone"], tone, network)

    def test_price_formatting_has_no_trailing_zeros(self):
        self.assertEqual(self.ctx(sale=self.sale("60.00"))["sale"]["price"], "60")
        self.assertEqual(self.ctx(sale=self.sale("60.50"))["sale"]["price"], "60.5")
        self.assertEqual(self.ctx(sale=self.sale("7.25"))["sale"]["price"], "7.25")

    def test_empty_info_renders_dashes_and_no_usage(self):
        ctx = self.ctx(NumberInfo())
        self.assertIsNone(ctx["expiry"])
        self.assertIsNone(ctx["sale"])
        self.assertFalse(ctx["has_usage"])
        html = self.card(NumberInfo(), sale=None)
        self.assertIn("لا تتوفر بيانات استهلاك لهذا الرقم", html)
        self.assertIn("لا توجد بيعة سابقة لهذا الرقم", html)
        self.assertNotIn("data-copy", html)

    def test_expiry_countdown_tones(self):
        def expiry(day):
            ends = dt.datetime(2026, 10, day, 12, 0, tzinfo=TZ_PLUS_2)
            return self.ctx(NumberInfo(ends_at=ends))["expiry"]

        self.assertEqual((expiry(15)["days"], expiry(15)["tone"]), ("1", "danger"))
        self.assertEqual((expiry(19)["days"], expiry(19)["word"], expiry(19)["tone"]), ("5", "أيام", "warn"))
        self.assertEqual(expiry(25)["tone"], "ok")

    def test_expiry_today(self):
        ends = dt.datetime(2026, 10, 14, 12, 0, tzinfo=TZ_PLUS_2)
        expiry = self.ctx(NumberInfo(ends_at=ends))["expiry"]
        self.assertEqual((expiry["date"], expiry["days"], expiry["state"]), ("14/10/2026", "", "تنتهي اليوم"))

    def test_expiry_past(self):
        ends = dt.datetime(2026, 10, 1, 12, 0, tzinfo=TZ_PLUS_2)
        expiry = self.ctx(NumberInfo(ends_at=ends))["expiry"]
        self.assertEqual((expiry["date"], expiry["state"], expiry["tone"]), ("01/10/2026", "منتهية", "danger"))

    def test_expiry_uses_the_local_date_of_the_end_time(self):
        # 22:30 UTC on the 13th is already the 14th in Asia/Jerusalem (UTC+3 in
        # summer / +2 in winter): the local date decides, not the offset in the data.
        ends = dt.datetime(2026, 11, 13, 22, 30, tzinfo=dt.timezone.utc)
        expiry = self.ctx(NumberInfo(ends_at=ends))["expiry"]
        self.assertEqual((expiry["date"], expiry["days"]), ("14/11/2026", "31"))

    def test_sale_date_is_the_local_date(self):
        created = dt.datetime(2026, 10, 13, 22, 30, tzinfo=dt.timezone.utc)
        self.assertEqual(self.ctx(sale=self.sale(created=created))["sale"]["date"], "14/10/2026")

    def test_provider_strings_are_escaped_in_the_card(self):
        html = self.card(NumberInfo(network="<b>x</b>", plan_name="<script>alert(1)</script>"))
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", html)
        self.assertNotIn("<b>x</b>", html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_card_shows_every_field(self):
        html = self.card()
        for text in (
            "سلكوم",
            "rd-ncheck--cellcom",
            "شريحة إلكترونية",
            "مفعل",
            "حملة رمضان 400 جيجا سلكوم",
            "13/11/2026",
            "متبقي",
            "الاستهلاك",
            "إنترنت",
            "دقائق",
            "رسائل",
            "سعر البيع",
            "سلكوم — رمضان",
            "آخر بيعة 14/10/2026",
            "رقم الشريحة",
            "8997 2012 3456 7890 123",
            f'data-copy="{ICCID}"',
        ):
            self.assertIn(text, html, text)
        self.assertIn('<span class="rd-ncheck__big-num">30</span>', html)
        self.assertRegex(html, r"<bdi>60</bdi>")
        self.assertEqual(html.count('class="rd-ncheck__row '), 3)
        self.assertEqual(html.count("<progress "), 3)
        self.assertIn('value="1.5"', html)

    def test_card_has_no_inline_style(self):
        self.assertNotIn("style=", self.card())

    def test_card_never_mentions_the_provider_price(self):
        html = self.card()
        self.assertNotIn("29", _visible(html))
        self.assertNotIn("29", json.dumps(_share_json(html), ensure_ascii=False))
        self.assertNotIn("plan_price", html)

    # --- phone, line logo, share payload -----------------------------------

    def test_phone_is_grouped_three_three_four(self):
        self.assertEqual(self.ctx(phone="0525737739")["phone_display"], "052-573-7739")

    def test_phone_that_is_not_ten_digits_is_shown_as_is(self):
        self.assertEqual(self.ctx(phone="12345")["phone_display"], "12345")
        self.assertEqual(self.ctx(phone="+972525737739")["phone_display"], "+972525737739")
        self.assertEqual(self.ctx(phone="")["phone_display"], "")

    def test_phone_defaults_to_empty(self):
        ctx = card_context(INFO, None, TODAY)
        self.assertEqual((ctx["phone"], ctx["phone_display"], ctx["share"]["checked_at"]), ("", "", ""))

    def test_line_logo_comes_from_the_sale_product_line_without_a_query(self):
        line = self.line_with_icon("سلكوم 2", icon="icons/product_lines/own.webp")
        product = Product.objects.create(
            line=line, cost_price=Decimal("5"), default_sell_price=Decimal("60")
        )
        sale = Sale(product=product, company=self.company, created_at=NOW)
        with self.assertNumQueries(0):
            ctx = self.ctx(sale=sale)
        self.assertTrue(ctx["line_logo"].endswith("icons/product_lines/own.webp"), ctx["line_logo"])
        self.assertEqual(ctx["line_name"], "سلكوم 2")
        self.assertEqual(ctx["share"]["line_logo"], ctx["line_logo"])

    def test_line_logo_falls_back_to_a_line_matching_the_network_name(self):
        self.line_with_icon("سلكوم", icon="icons/product_lines/cellcom.webp")
        with self.assertNumQueries(1):
            ctx = self.ctx(sale=None)
        self.assertTrue(ctx["line_logo"].endswith("icons/product_lines/cellcom.webp"))
        self.assertEqual(ctx["line_name"], "سلكوم")

    def test_fallback_runs_when_the_sale_line_has_no_icon(self):
        self.line_with_icon("سلكوم", icon="icons/product_lines/other.webp")
        ctx = self.ctx(sale=self.sale())  # self.product's line has no icon
        self.assertTrue(ctx["line_logo"].endswith("icons/product_lines/other.webp"))

    def test_fallback_matches_by_network_tone_ignoring_spaces_and_hyphens(self):
        self.line_with_icon("Cell-com 019", icon="icons/product_lines/c.webp")
        self.assertTrue(self.ctx(NumberInfo(network="سلكوم"))["line_logo"].endswith("c.webp"))

    def test_fallback_matches_by_name_containment(self):
        self.line_with_icon("مزود جديد - باقات", icon="icons/product_lines/n.webp")
        self.assertTrue(self.ctx(NumberInfo(network="مزود  جديد"))["line_logo"].endswith("n.webp"))

    def test_fallback_ignores_lines_without_an_icon_and_other_networks(self):
        ProductLine.objects.create(company=self.company, name="سلكوم 2")
        self.line_with_icon("هوت", icon="icons/product_lines/hot.webp")
        ctx = self.ctx(sale=None)
        self.assertEqual((ctx["line_logo"], ctx["line_name"]), ("", ""))

    def test_no_network_means_no_logo_and_no_query(self):
        self.line_with_icon("سلكوم")
        with self.assertNumQueries(0):
            ctx = self.ctx(NumberInfo(), sale=None)
        self.assertEqual(ctx["line_logo"], "")

    def test_share_holds_only_customer_facing_values(self):
        sale = self.sale("77.5", created=dt.datetime(2026, 9, 1, 9, 0, tzinfo=TZ_PLUS_2))
        share = self.ctx(sale=sale)["share"]
        self.assertEqual(
            set(share),
            {
                "phone_display", "network", "sim_type", "status", "status_tone", "plan_name",
                "expiry", "usage", "utilization", "line_logo", "checked_at",
            },
        )
        self.assertEqual(share["phone_display"], PHONE_DISPLAY)
        self.assertEqual((share["network"], share["sim_type"]), ("سلكوم", "شريحة إلكترونية"))
        self.assertEqual((share["status"], share["status_tone"]), ("مفعل", "ok"))
        self.assertEqual(share["plan_name"], "حملة رمضان 400 جيجا سلكوم")
        self.assertEqual(share["expiry"], self.ctx()["expiry"])
        self.assertEqual(share["utilization"], "1.5")
        self.assertEqual(share["checked_at"], timezone.localtime(NOW).strftime("%d/%m/%Y · %H:%M"))
        self.assertRegex(share["checked_at"], r"^14/10/2026 · \d\d:05$")
        self.assertEqual(
            share["usage"][0],
            {"label": "إنترنت", "used": "6.08", "total": "400", "unit": "جيجا", "pct": "1.5", "included": True},
        )
        self.assertEqual(len(share["usage"]), 3)
        text = json.dumps(share, ensure_ascii=False)
        for secret in ("77.5", ICCID, "8997", "سلكوم — رمضان", "01/09/2026", "plan_price", "iccid", "price", "sale", "product", "29"):
            self.assertNotIn(secret, text, secret)

    def test_share_payload_in_the_card_has_no_price_iccid_or_sale(self):
        sale = self.sale("77.5", created=dt.datetime(2026, 9, 1, 9, 0, tzinfo=TZ_PLUS_2))
        html = self.card(sale=sale)
        payload = SHARE_RE.search(html).group(1)
        for secret in ("77.5", ICCID, "8997", "01/09/2026", "plan_price", "iccid"):
            self.assertNotIn(secret, payload, secret)
        self.assertNotIn("سلكوم — رمضان", json.dumps(_share_json(html), ensure_ascii=False))

    # --- card markup -----------------------------------------------------------

    def test_number_is_shown_ltr_and_grouped(self):
        self.assertIn(f'<div class="rd-ncheck__num tabular-nums" dir="ltr">{PHONE_DISPLAY}</div>', self.card())

    def test_head_shows_network_and_sim_type_under_the_number(self):
        self.assertIn("سلكوم · شريحة إلكترونية", self.card())

    def test_line_logo_is_an_img_with_alt(self):
        self.line_with_icon("سلكوم", icon="icons/product_lines/cellcom.webp")
        html = self.card(sale=None)
        self.assertRegex(html, r'<img class="rd-ncheck__logo-img" src="[^"]*cellcom\.webp" alt="سلكوم"')
        self.assertNotIn("rd-ncheck__mono", html)

    def test_monogram_tile_when_there_is_no_logo(self):
        html = self.card(sale=None)
        self.assertNotIn("<img", html)
        self.assertRegex(html, r'<span class="rd-ncheck__mono" aria-hidden="true">س</span>')

    def test_monogram_without_a_network_is_a_dash(self):
        self.assertRegex(self.card(NumberInfo(), sale=None), r'rd-ncheck__mono" aria-hidden="true">—<')

    def test_status_pill_has_dot_text_and_tone(self):
        html = self.card()
        self.assertRegex(html, r'<span class="rd-ncheck__pill is-ok"[^>]*>\s*<span class="rd-ncheck__dot" aria-hidden="true"></span>\s*مفعل\s*</span>')
        warn = self.card(NumberInfo(status="موقوف"))
        self.assertIn("rd-ncheck__pill is-warn", warn)
        self.assertIn("rd-ncheck__pill is-muted", self.card(NumberInfo()))

    def test_plan_line(self):
        self.assertRegex(self.card(), r"الحزمة: <b>حملة رمضان 400 جيجا سلكوم</b>")

    def test_days_box_shows_the_count_the_word_and_the_ltr_end_date(self):
        html = self.card()
        self.assertRegex(html, r'rd-ncheck__box is-ok[^>]*>\s*<div class="rd-ncheck__box-label">الأيام المتبقية</div>')
        self.assertIn('<span class="rd-ncheck__big-num">30</span><span class="rd-ncheck__big-word">يوم</span>', html)
        self.assertIn('<div class="rd-ncheck__date tabular-nums" dir="ltr">13/11/2026</div>', html)

    def test_days_box_tone_follows_the_expiry(self):
        def box(day):
            ends = dt.datetime(2026, 10, day, 12, 0, tzinfo=TZ_PLUS_2)
            return self.card(NumberInfo(ends_at=ends))
        self.assertIn("rd-ncheck__box is-danger", box(15))
        self.assertIn("rd-ncheck__box is-warn", box(19))

    def test_expired_card_shows_the_state_instead_of_a_count(self):
        ends = dt.datetime(2026, 10, 1, 12, 0, tzinfo=TZ_PLUS_2)
        html = self.card(NumberInfo(ends_at=ends))
        self.assertIn('<span class="rd-ncheck__state">منتهية</span>', html)
        self.assertIn("rd-ncheck__box is-danger", html)
        self.assertNotIn("rd-ncheck__big-num", html)
        self.assertIn(">01/10/2026<", html)

    def test_expiring_today_card_shows_the_state(self):
        ends = dt.datetime(2026, 10, 14, 12, 0, tzinfo=TZ_PLUS_2)
        html = self.card(NumberInfo(ends_at=ends))
        self.assertIn('<span class="rd-ncheck__state">تنتهي اليوم</span>', html)
        self.assertIn(">14/10/2026<", html)

    def test_no_expiry_shows_one_muted_box(self):
        html = self.card(NumberInfo())
        self.assertEqual(html.count("rd-ncheck__box "), 1)
        self.assertIn("rd-ncheck__box is-muted", html)
        self.assertIn("لا يوجد تاريخ انتهاء", html)
        self.assertNotIn("الأيام المتبقية", html)

    def test_usage_rows_show_value_remaining_and_percent(self):
        html = self.card()
        self.assertIn("<bdi dir=\"ltr\">6.08 / 400</bdi>", html)
        self.assertIn("جيجا", html)
        self.assertIn("<bdi dir=\"ltr\">0 / 5000</bdi>", html)
        self.assertIn("دقيقة", html)
        self.assertIn("رسالة", html)
        self.assertIn("متبقي <bdi dir=\"ltr\">393.92</bdi>", html)
        self.assertIn("متبقي <bdi dir=\"ltr\">5000</bdi>", html)
        self.assertIn("<bdi dir=\"ltr\">1.5%</bdi>", html)  # row pct and the utilization line
        self.assertIn("نسبة الاستخدام", html)
        self.assertIn('aria-label="إنترنت: 6.08 من 400 جيجا"', html)

    def test_usage_row_without_total_shows_only_the_used_value(self):
        html = self.card(NumberInfo(data_used_gb=Decimal("1.50")))
        self.assertIn('<bdi dir="ltr">1.5</bdi>', html)
        self.assertNotIn("1.5 /", html)

    def test_not_included_row_has_no_bar(self):
        html = self.card(NumberInfo(sms_used=0, sms_total=0))
        self.assertIn("غير مشمولة بالحزمة", html)
        self.assertEqual(html.count("<progress "), 0)

    def test_footer_keeps_price_product_date_iccid_and_copy(self):
        html = self.card()
        for text in ("سعر البيع", "<bdi>60</bdi>", "₪", "سلكوم — رمضان", "آخر بيعة 14/10/2026",
                     "رقم الشريحة", "8997 2012 3456 7890 123", f'data-copy="{ICCID}"',
                     'data-done-label="تم النسخ"'):
            self.assertIn(text, html, text)

    def test_footer_without_a_sale(self):
        html = self.card(sale=None)
        self.assertIn("لا توجد بيعة سابقة لهذا الرقم", html)
        self.assertNotIn("<bdi>60</bdi>", html)

    def test_share_button_and_data(self):
        html = self.card()
        self.assertRegex(html, r'<button type="button" class="btn btn-primary rd-ncheck__share" data-rd-share-check>')
        self.assertIn("مشاركة كصورة", html)
        self.assertIn('data-rd-share-label', html)
        self.assertEqual(html.count('id="rd-ncheck-share-data"'), 1)
        self.assertEqual(_share_json(html)["phone_display"], PHONE_DISPLAY)

    def test_price_and_iccid_are_outside_the_share_payload(self):
        html = self.card()
        payload = SHARE_RE.search(html).group(1)
        self.assertIn("<bdi>60</bdi>", _visible(html))
        for secret in ("60", ICCID, "8997 2012", "سلكوم — رمضان"):
            self.assertNotIn(secret, payload, secret)

    def test_card_exposes_site_branding_for_the_image(self):
        html = render_to_string(
            "sales/partials/number_check_card.html",
            {**self.ctx(), "site_logo": "/media/branding/logo.webp", "site_name": "Pro Sim"},
        )
        self.assertIn('data-site-logo="/media/branding/logo.webp"', html)
        self.assertIn('data-site-name="Pro Sim"', html)

    def test_helper_module_does_not_leak_info_fields(self):
        self.assertNotIn("plan_price", self.ctx(INFO, self.sale()))
        self.assertTrue(callable(number_check.submit_number_check))
