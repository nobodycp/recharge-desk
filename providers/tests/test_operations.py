from __future__ import annotations

import datetime as dt
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import DatabaseError, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from providers import operations
from providers.contracts import (
    AuthRequired,
    ChargeOutcome,
    ChargeResult,
    PermanentError,
    TransientError,
)
from providers.models import ProviderJob, ProviderOperation
from providers.operations import Classified
from providers.tasks import purge_old_jobs


class ClassifyCodeTests(TestCase):
    def test_login_codes(self):
        for code in ("login_failed", "login_error", "otp_required", "auth_required"):
            with self.subTest(code=code):
                self.assertEqual(
                    operations.classify_code("charge", code),
                    Classified("failed", "login_failed", "فشل الشحن — خطأ تسجيل الدخول"),
                )

    def test_session_errors_after_login_map_to_login_failed(self):
        # AuthRequired texts that persist after a login (adapter/service refresh path).
        for code in ("session_expired", "session not ready"):
            with self.subTest(code=code):
                result = operations.classify_code("refresh", code)
                self.assertEqual(result.error_code, "login_failed")
                self.assertIn("خطأ تسجيل الدخول", result.message)
                self.assertEqual(
                    result, Classified("failed", "login_failed", "فشل التحديث — خطأ تسجيل الدخول")
                )

    def test_not_sent_prefix_is_stripped(self):
        self.assertEqual(
            operations.classify_code("charge", "not_sent:login_failed"),
            Classified("failed", "login_failed", "فشل الشحن — خطأ تسجيل الدخول"),
        )

    def test_login_busy(self):
        self.assertEqual(
            operations.classify_code("refresh", "login_busy"),
            Classified(
                "failed",
                "login_busy",
                "فشل التحديث — تسجيل الدخول قيد التنفيذ، حاول بعد قليل",
            ),
        )

    def test_provider_disabled_is_service_off(self):
        self.assertEqual(
            operations.classify_code("refresh", "provider_disabled"),
            Classified("failed", "service_off", "فشل التحديث — الخدمة موقوفة"),
        )

    def test_not_configured_codes(self):
        for code in ("config_error", "not_configured"):
            with self.subTest(code=code):
                self.assertEqual(
                    operations.classify_code("reconcile", code),
                    Classified(
                        "failed", "not_configured", "فشل المطابقة — بيانات الحساب ناقصة"
                    ),
                )

    def test_connection_codes(self):
        for code in (
            "connection_error",
            "proxy_error",
            "session_error",
            "sync_budget_exceeded",
            "timeout",
        ):
            with self.subTest(code=code):
                self.assertEqual(
                    operations.classify_code("number_info", code),
                    Classified(
                        "failed",
                        "connection",
                        "فشل فحص الرقم — تعذّر الاتصال بالمزوّد",
                    ),
                )

    def test_rejected_reasons(self):
        for code, reason in (
            ("not_found", "الرقم غير موجود"),
            ("not_active", "الرقم غير مفعّل"),
            ("not_eligible", "الرقم غير مؤهّل"),
        ):
            with self.subTest(code=code):
                self.assertEqual(
                    operations.classify_code("refresh", code),
                    Classified(
                        "failed", "rejected", f"فشل التحديث — رفض المزوّد: {reason}"
                    ),
                )

    def test_internal_codes(self):
        for code in ("context_error", "unknown_provider"):
            with self.subTest(code=code):
                self.assertEqual(
                    operations.classify_code("check_charge", code),
                    Classified("failed", "internal", "فشل متابعة الشحن — خطأ داخلي"),
                )

    def test_anything_else_is_provider_error(self):
        for code in ("something weird", "api_error", ""):
            with self.subTest(code=code):
                result = operations.classify_code("refresh", code)
                self.assertEqual(result.error_code, "provider_error")
                self.assertEqual(
                    result.message, "فشل التحديث — خطأ في عملية التحديث"
                )


class SuccessTests(TestCase):
    def test_success_with_ref(self):
        self.assertEqual(
            operations.success("charge", "123"),
            Classified("success", "", "تمّ الشحن — رقم العملية 123"),
        )

    def test_success_phrase_per_service(self):
        for service, phrase in (
            ("refresh", "تمّ التحديث"),
            ("number_info", "تمّ فحص الرقم"),
            ("charge", "تمّ الشحن"),
            ("check_charge", "تمّت متابعة الشحن"),
            ("reconcile", "تمّت المطابقة"),
        ):
            with self.subTest(service=service):
                self.assertEqual(operations.success(service).message, phrase)
                self.assertEqual(
                    operations.success(service, "9").message,
                    f"{phrase} — رقم العملية 9",
                )
        self.assertEqual(set(operations.SUCCESS_LABELS), set(operations.SERVICE_LABELS))

    def test_success_unknown_service_falls_back(self):
        self.assertEqual(operations.success("other").message, "تم other")

    def test_rejected_helper(self):
        self.assertEqual(
            operations.rejected("refresh", "رقم خاطئ"),
            Classified("failed", "rejected", "فشل التحديث — رفض المزوّد: رقم خاطئ"),
        )


class ClassifyExceptionTests(TestCase):
    def test_transient_and_permanent_use_the_code(self):
        self.assertEqual(
            operations.classify_exception("charge", TransientError("login_busy")).error_code,
            "login_busy",
        )
        self.assertEqual(
            operations.classify_exception(
                "refresh", PermanentError("provider_disabled")
            ).error_code,
            "service_off",
        )

    def test_auth_required_is_login_failed(self):
        self.assertEqual(
            operations.classify_exception("charge", AuthRequired()),
            Classified("failed", "login_failed", "فشل الشحن — خطأ تسجيل الدخول"),
        )
        self.assertEqual(
            operations.classify_exception("charge", AuthRequired("whatever")).error_code,
            "login_failed",
        )

    def test_other_exception_is_internal(self):
        self.assertEqual(
            operations.classify_exception("refresh", ValueError("x")),
            Classified("failed", "internal", "فشل التحديث — خطأ داخلي"),
        )


class ClassifyChargeTests(TestCase):
    def test_success(self):
        outcome = ChargeOutcome(ChargeResult.SUCCESS, provider_ref="R9")
        self.assertEqual(
            operations.classify_charge("charge", outcome),
            Classified("success", "", "تمّ الشحن — رقم العملية R9"),
        )

    def test_unverified_is_pending(self):
        outcome = ChargeOutcome(ChargeResult.UNVERIFIED)
        self.assertEqual(
            operations.classify_charge("charge", outcome),
            Classified("pending", "", "الشحن قيد المتابعة"),
        )

    def test_failed_uses_message_code(self):
        outcome = ChargeOutcome(ChargeResult.FAILED, message="not_sent:login_busy")
        self.assertEqual(
            operations.classify_charge("charge", outcome),
            Classified(
                "failed",
                "login_busy",
                "فشل الشحن — تسجيل الدخول قيد التنفيذ، حاول بعد قليل",
            ),
        )


class LabelCoverageTests(TestCase):
    def test_labels_cover_everything_the_classifier_emits(self):
        self.assertEqual(
            operations.SOURCES,
            ("web", "sms", "api", "employee", "management", "system"),
        )
        self.assertEqual(tuple(operations.SOURCE_LABELS), operations.SOURCES)
        self.assertEqual(
            set(operations.OUTCOME_LABELS),
            {
                operations.OUTCOME_SUCCESS,
                operations.OUTCOME_FAILED,
                operations.OUTCOME_PENDING,
            },
        )
        codes = [
            "login_failed", "login_busy", "provider_disabled", "config_error",
            "timeout", "not_found", "not_active", "not_eligible", "context_error",
            "weird",
        ]
        emitted = set()
        outcomes = set()
        for service in operations.SERVICE_LABELS:
            for code in codes:
                result = operations.classify_code(service, code)
                emitted.add(result.error_code)
                outcomes.add(result.outcome)
            exc = operations.classify_exception(service, ValueError("x"))
            emitted.add(exc.error_code)
            outcomes.add(exc.outcome)
        for charge_result in ChargeResult:
            result = operations.classify_charge(
                "charge", ChargeOutcome(charge_result, message="login_failed")
            )
            outcomes.add(result.outcome)
            if result.error_code:
                emitted.add(result.error_code)
        self.assertLessEqual(emitted, set(operations.CAUSE_LABELS))
        self.assertLessEqual(outcomes, set(operations.OUTCOME_LABELS))
        self.assertIn("price_mismatch", operations.CAUSE_LABELS)
        self.assertEqual(operations.CAUSE_LABELS["rejected"], "رفض المزوّد")
        self.assertEqual(operations.CAUSE_LABELS["provider_error"], "خطأ في العملية")
        self.assertEqual(operations.CAUSE_LABELS["price_mismatch"], "السعر غير مطابق")


class RecordTests(TestCase):
    def test_record_writes_one_row_with_all_fields(self):
        user = get_user_model().objects.create_user(username="emp", password="x")
        job_id = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
        operations.record(
            "sky",
            "charge",
            operations.success("charge", "55"),
            phone="0599000000",
            source="employee",
            user_id=user.id,
            sale_id=7,
            job_id=job_id,
            attempts=2,
            duration_ms=1234,
            detail="ok",
        )
        row = ProviderOperation.objects.get()
        self.assertEqual(row.provider_key, "sky")
        self.assertEqual(row.service, "charge")
        self.assertEqual(row.phone, "0599000000")
        self.assertEqual(row.outcome, "success")
        self.assertEqual(row.error_code, "")
        self.assertEqual(row.message, "تمّ الشحن — رقم العملية 55")
        self.assertEqual(row.source, "employee")
        self.assertEqual(row.user_id, user.id)
        self.assertEqual(row.sale_id, 7)
        self.assertEqual(str(row.job_id), job_id)
        self.assertEqual(row.attempts, 2)
        self.assertEqual(row.duration_ms, 1234)
        self.assertEqual(row.detail, "ok")
        self.assertIsNotNone(row.created_at)

    def test_record_defaults(self):
        operations.record("sky", "reconcile", operations.success("reconcile"))
        row = ProviderOperation.objects.get()
        self.assertEqual(row.source, "system")
        self.assertEqual(row.phone, "")
        self.assertIsNone(row.user_id)
        self.assertIsNone(row.sale_id)
        self.assertIsNone(row.job_id)
        self.assertEqual(row.attempts, 1)
        self.assertIsNone(row.duration_ms)

    def test_detail_is_scrubbed_and_truncated(self):
        operations.record(
            "sky",
            "refresh",
            operations.classify_code("refresh", "login_failed"),
            detail="password=hunter2 " + "x" * 800,
        )
        row = ProviderOperation.objects.get()
        self.assertNotIn("hunter2", row.detail)
        self.assertIn("password=***", row.detail)
        self.assertLessEqual(len(row.detail), 500)

    def test_secret_straddling_the_limit_is_scrubbed_before_truncating(self):
        operations.record(
            "sky",
            "refresh",
            operations.classify_code("refresh", "login_failed"),
            detail="x" * 490 + " password=hunter2",
        )
        detail = ProviderOperation.objects.get().detail
        self.assertNotIn("hunter2", detail)
        self.assertLessEqual(len(detail), 500)

    def test_invalid_job_id_or_user_id_never_raises(self):
        for kwargs in ({"job_id": "not-a-uuid"}, {"user_id": "abc"}):
            with self.subTest(kwargs=kwargs):
                with self.assertLogs("providers.operations", level="WARNING"):
                    result = operations.record(
                        "sky", "charge", operations.success("charge"), **kwargs
                    )
                self.assertIsNone(result)
                self.assertEqual(ProviderOperation.objects.count(), 0)

    def test_unknown_source_is_stored_as_system_with_warning(self):
        with self.assertLogs("providers.operations", level="WARNING"):
            operations.record(
                "sky", "charge", operations.success("charge"), source="bogus"
            )
        self.assertEqual(ProviderOperation.objects.get().source, "system")

    def test_unknown_outcome_is_stored_as_failed_with_warning(self):
        with self.assertLogs("providers.operations", level="WARNING"):
            operations.record(
                "sky", "charge", Classified("weird", "internal", "فشل الشحن — خطأ داخلي")
            )
        self.assertEqual(ProviderOperation.objects.get().outcome, "failed")

    def test_known_source_and_outcome_do_not_warn(self):
        with mock.patch.object(operations.log, "warning") as warn:
            operations.record(
                "sky", "charge", operations.success("charge"), source="api"
            )
        warn.assert_not_called()
        row = ProviderOperation.objects.get()
        self.assertEqual((row.source, row.outcome), ("api", "success"))

    def test_phone_is_truncated_to_the_model_max_length(self):
        operations.record(
            "sky", "refresh", operations.success("refresh"), phone="9" * 64
        )
        row = ProviderOperation.objects.get()
        self.assertEqual(len(row.phone), 20)
        self.assertEqual(row.phone, "9" * 20)

    def test_message_is_truncated_to_255(self):
        operations.record(
            "sky", "refresh", Classified("failed", "provider_error", "م" * 400)
        )
        self.assertEqual(len(ProviderOperation.objects.get().message), 255)

    def test_db_error_is_swallowed_and_logged(self):
        with mock.patch.object(
            ProviderOperation.objects, "create", side_effect=DatabaseError("boom")
        ):
            with self.assertLogs("providers.operations", level="WARNING") as logs:
                result = operations.record(
                    "sky", "charge", operations.success("charge")
                )
        self.assertIsNone(result)
        self.assertTrue(any("boom" in line for line in logs.output))
        self.assertEqual(ProviderOperation.objects.count(), 0)


class RecordSavepointTests(TransactionTestCase):
    # A real (non-wrapped) transaction so the outer commit is observable.
    def test_failed_insert_does_not_poison_the_callers_transaction(self):
        def partial_write_then_fail(**kwargs):
            get_user_model().objects.create_user(username="partial")
            raise DatabaseError("boom")

        with transaction.atomic():
            with mock.patch.object(
                ProviderOperation.objects, "create", side_effect=partial_write_then_fail
            ):
                operations.record("sky", "charge", operations.success("charge"))
            # The outer transaction is still usable and nothing was marked for rollback.
            self.assertFalse(transaction.get_connection().needs_rollback)
            get_user_model().objects.create_user(username="outer")

        users = get_user_model().objects
        self.assertTrue(users.filter(username="outer").exists())
        # The failed record's partial write was rolled back with its savepoint.
        self.assertFalse(users.filter(username="partial").exists())
        self.assertEqual(ProviderOperation.objects.count(), 0)


class PurgeTests(TestCase):
    @override_settings(PROVIDER_OPERATIONS_RETENTION_DAYS=90)
    def test_purge_old_jobs_also_purges_old_operations(self):
        old = ProviderOperation.objects.create(
            provider_key="sky", service="refresh", outcome="success", message="m"
        )
        new = ProviderOperation.objects.create(
            provider_key="sky", service="refresh", outcome="success", message="m"
        )
        now = timezone.now()
        ProviderOperation.objects.filter(pk=old.pk).update(
            created_at=now - dt.timedelta(days=91)
        )
        ProviderOperation.objects.filter(pk=new.pk).update(
            created_at=now - dt.timedelta(days=89)
        )
        old_job = ProviderJob.objects.create(
            kind="refresh", provider_key="sky", phone="0529000000"
        )
        new_job = ProviderJob.objects.create(
            kind="refresh", provider_key="sky", phone="0529000001"
        )
        ProviderJob.objects.filter(pk=old_job.pk).update(
            created_at=now - dt.timedelta(days=31)
        )
        with self.assertLogs("providers.tasks", level="INFO") as logs:
            deleted = purge_old_jobs()
        # The return value keeps its old contract: refresh jobs only.
        self.assertEqual(deleted, 1)
        self.assertTrue(any("1" in line and "operation" in line for line in logs.output))
        self.assertEqual(
            list(ProviderOperation.objects.values_list("pk", flat=True)), [new.pk]
        )
        self.assertEqual(
            list(ProviderJob.objects.values_list("pk", flat=True)), [new_job.pk]
        )
