"""Charge-and-record-sale: money safety around the provider charge."""
from __future__ import annotations

import datetime as dt
import itertools
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserProfile
from audit.models import AuditAction, AuditLog
from companies.models import Company, Product, ProductLine
from providers.contracts import (
    AuthRequired,
    Capability,
    ChargeOutcome,
    ChargeResult,
    PermanentError,
    TransientError,
)
from providers.crypto import encrypt
from providers.models import ProviderConnection, ProviderJob, ProviderOperation
from providers.redis_conn import get_redis
from providers.registry import ADAPTERS
from providers.tests.fakes import FakeChargeAdapter
from sales import charging
from sales.employee_editing import employee_editable_sale_ids, sale_is_employee_editable
from sales.models import CompanyBalanceTransaction, PaymentMethod, Sale
from sales.services import cancel_sale, delete_sale_permanently, update_sale_fields
from sales.tasks import check_sale_charge, run_charge_job, verify_unverified_charges

User = get_user_model()
START = Decimal("1000.00")
COST = Decimal("10.00")


def _save_credentials(key):
    connection = ProviderConnection.for_key(key)
    connection.username = "user"
    connection.secret_encrypted = encrypt("pass")
    connection.save()
    return connection


class ChargingTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("cashier", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={
                "role": UserProfile.Role.EMPLOYEE,
                "is_active_profile": True,
                "can_provider_charge": True,
            },
        )
        cls.manager = User.objects.create_user("boss", password="x")
        UserProfile.objects.update_or_create(
            user=cls.manager,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        cls.company = Company.objects.create(
            name="FakeCo", provider_key="fake", current_balance=START
        )
        line = ProductLine.objects.create(company=cls.company, name="Line")
        cls.product = Product.objects.create(
            line=line, cost_price=COST, default_sell_price=Decimal("20")
        )
        cls.pm = PaymentMethod.objects.create(name="Cash")

    def setUp(self):
        get_redis().flushall()
        self.use()
        connection = ProviderConnection.for_key("fake")
        connection.charge_enabled = True
        connection.save()

    def use(self, **options):
        self.fake = FakeChargeAdapter.configured(**options)
        patcher = patch.dict(ADAPTERS, {"fake": self.fake})
        patcher.start()
        self.addCleanup(patcher.stop)
        return self.fake

    def sale_kwargs(self, **overrides):
        kwargs = dict(
            company=self.company,
            product=self.product,
            reference_number="0599123456",
            payer_name="Walk-in",
            payment_method=self.pm,
            sell_price_actual=Decimal("20"),
            notes="",
        )
        kwargs.update(overrides)
        return kwargs

    def charge_with_outcome(self, token="tok-1", **overrides):
        with self.captureOnCommitCallbacks(execute=True):
            sale, outcome = charging.create_charged_sale(
                form_token=token, user=self.user, **self.sale_kwargs(**overrides)
            )
        try:
            sale.refresh_from_db()
        except Sale.DoesNotExist:
            pass
        return sale, outcome

    def charge(self, token="tok-1", **overrides) -> Sale:
        return self.charge_with_outcome(token, **overrides)[0]

    def unverified(self, **options) -> Sale:
        self.use(raise_after_send=TransientError("timeout"), **options)
        with self.assertLogs("providers.service", level="WARNING"):
            return self.charge()

    def charge_without_worker(self, token="tok-1") -> Sale:
        """Create the sale while the broker is down: the job stays queued."""
        with patch(
            "sales.tasks.run_charge_job.delay", side_effect=ConnectionError("broker down")
        ), self.assertLogs("sales.charging", level="ERROR"):
            sale = self.charge(token)
        return sale

    def set_job(self, sale, **fields) -> None:
        ProviderJob.objects.filter(pk=sale.charge_job_id).update(**fields)

    def job(self, sale) -> ProviderJob:
        return ProviderJob.objects.get(pk=sale.charge_job_id)

    def balance(self) -> Decimal:
        self.company.refresh_from_db()
        return self.company.current_balance

    def reversals(self, sale) -> int:
        return CompanyBalanceTransaction.objects.filter(
            entry_type=CompanyBalanceTransaction.EntryType.REVERSAL,
            reference_id=sale.pk,
        ).count()

    def deductions(self, sale) -> int:
        return CompanyBalanceTransaction.objects.filter(
            entry_type=CompanyBalanceTransaction.EntryType.DEDUCTION,
            reference_id=sale.pk,
        ).count()

    def age(self, sale, minutes: int) -> None:
        Sale.objects.filter(pk=sale.pk).update(
            created_at=timezone.now() - dt.timedelta(minutes=minutes)
        )

    def checked_ago(self, sale, minutes: int) -> None:
        Sale.objects.filter(pk=sale.pk).update(
            charge_checked_at=timezone.now() - dt.timedelta(minutes=minutes)
        )


class ChargeFlowTests(ChargingTestBase):
    def test_success_sets_fields_and_keeps_deduction(self):
        self.use(provider_ref="R-77", plan_name="Unlimited", provider_cost=COST)
        sale = self.charge()
        self.assertEqual(sale.entry_mode, Sale.EntryMode.AUTO_CHARGE)
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.SUCCESS)
        self.assertEqual(sale.charge_provider_ref, "R-77")
        self.assertEqual(sale.charge_plan_name, "Unlimited")
        self.assertEqual(sale.charge_provider_cost, COST)
        self.assertIsNotNone(sale.charge_checked_at)
        self.assertFalse(sale.charge_conflict)
        self.assertEqual(sale.status, Sale.Status.PENDING)
        self.assertEqual(self.balance(), START - COST)
        self.assertEqual(self.deductions(sale), 1)
        self.assertEqual(self.reversals(sale), 0)

        job = self.job(sale)
        self.assertEqual(job.kind, ProviderJob.KIND_CHARGE)
        self.assertEqual(job.status, ProviderJob.STATUS_DONE)
        self.assertEqual(job.idempotency_key, "tok-1")
        self.assertEqual(job.payload, {"sale_id": sale.pk})
        self.assertEqual(job.phone, "0599123456")
        self.assertEqual(job.source, "employee")

        (ctx,) = self.fake.charges
        self.assertEqual(ctx.sale_id, sale.pk)
        self.assertEqual(ctx.phone, "0599123456")
        self.assertEqual(ctx.expected_cost, COST)
        self.assertEqual(ctx.idempotency_key, "tok-1")
        self.assertEqual(ctx.requested_at, sale.created_at)
        self.assertTrue(
            AuditLog.objects.filter(
                object_id=str(sale.pk), changes___extra__charge_status="success"
            ).exists()
        )

    def test_returns_finalize_outcome_token(self):
        sale, outcome = self.charge_with_outcome("t-1")
        self.assertEqual(outcome, "pending_payment")
        again, outcome = self.charge_with_outcome("t-1")
        self.assertEqual(again.pk, sale.pk)
        self.assertEqual(outcome, "duplicate")

    def test_failed_cancels_sale_and_restores_balance_once(self):
        self.use(result=ChargeResult.FAILED, message="line not found")
        sale = self.charge()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.FAILED)
        self.assertEqual(sale.status, Sale.Status.CANCELLED)
        self.assertEqual(sale.charge_message, "فشل الشحن — خطأ في عملية الشحن")
        self.assertEqual(self.balance(), START)
        self.assertEqual(self.reversals(sale), 1)

        with self.assertLogs("sales.charging", level="INFO"):
            charging.apply_outcome(
                sale.pk, ChargeOutcome(result=ChargeResult.FAILED), actor=self.manager
            )
        self.assertEqual(self.reversals(sale), 1)
        self.assertEqual(self.balance(), START)

    def test_automatic_reversal_is_audited_as_system(self):
        self.use(result=ChargeResult.FAILED)
        sale = self.charge()
        cancel = AuditLog.objects.get(object_id=str(sale.pk), action=AuditAction.CANCEL)
        self.assertIsNone(cancel.actor)
        self.assertEqual(cancel.changes["_extra"]["actor"], "system")
        update = AuditLog.objects.get(
            object_id=str(sale.pk), changes___extra__charge_status="failed"
        )
        self.assertEqual(update.changes["_extra"]["actor"], "system")

    def test_transient_after_send_is_unverified_without_reversal(self):
        sale = self.unverified()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.UNVERIFIED)
        self.assertEqual(sale.status, Sale.Status.PENDING)
        self.assertEqual(self.balance(), START - COST)
        self.assertEqual(self.reversals(sale), 0)
        self.assertEqual(self.fake.sent, 1)

    def test_price_mismatch_is_kept_with_the_provider_reason(self):
        self.use(raise_before_send=PermanentError("price_mismatch"))
        sale = self.charge()
        sale.refresh_from_db()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.FAILED)
        self.assertEqual(sale.status, Sale.Status.CANCELLED)
        self.assertEqual(sale.charge_message, "فشل الشحن — السعر غير مطابق")
        self.assertEqual(self.balance(), START)
        self.assertEqual(self.fake.sent, 0)
        self.assertFalse(self.job(sale).result.get("discarded"))

    def test_charge_that_never_starts_is_dropped(self):
        self.use(raise_before_send=NotImplementedError())
        with self.assertLogs("providers.service", level="WARNING"):
            sale = self.charge()
        self.assertFalse(Sale.objects.filter(pk=sale.pk).exists())
        self.assertEqual(self.balance(), START)
        self.assertEqual(self.fake.sent, 0)
        self.assertTrue(self.job(sale).result.get("discarded"))

    def test_charge_never_sent_because_login_failed_reverses(self):
        _save_credentials("fake")
        self.use(
            needs_login=True,
            raise_before_send=AuthRequired("expired"),
            login_error=RuntimeError("otp rejected"),
        )
        with patch("providers.sessions.accounts.record_login"), self.assertLogs(
            "providers", level="WARNING"
        ):
            sale = self.charge()
        self.assertFalse(Sale.objects.filter(pk=sale.pk).exists())
        self.assertEqual(self.balance(), START)
        self.assertEqual(self.fake.sent, 0)
        row = ProviderOperation.objects.get()
        self.assertEqual((row.service, row.outcome, row.error_code), ("charge", "failed", "login_failed"))
        self.assertEqual(row.detail, "not_sent:login_failed")
        self.assertEqual(row.sale_id, sale.pk)
        self.assertEqual(row.user_id, self.user.pk)
        self.assertEqual(row.job_id, sale.charge_job_id)
        self.assertEqual(row.source, "employee")

    def test_charge_message_is_scrubbed_and_capped(self):
        self.use(result=ChargeResult.FAILED, message="rejected token=abc123 " + "x" * 400)
        sale = self.charge()
        self.assertNotIn("abc123", sale.charge_message)
        self.assertLessEqual(len(sale.charge_message), 255)

    def test_cost_mismatch_flag(self):
        self.use(provider_cost=Decimal("12.00"))
        self.assertTrue(self.charge("t-1").charge_cost_mismatch)
        self.use(provider_cost=COST)
        self.assertFalse(self.charge("t-2").charge_cost_mismatch)
        self.use(provider_cost=None)
        self.assertFalse(self.charge("t-3").charge_cost_mismatch)

    def test_rejects_company_that_cannot_charge(self):
        company = Company.objects.create(name="SkyCo", provider_key="sky")
        line = ProductLine.objects.create(company=company, name="L")
        product = Product.objects.create(
            line=line, cost_price=COST, default_sell_price=Decimal("20")
        )
        with self.assertRaises(ValueError):
            charging.create_charged_sale(
                form_token="t",
                user=self.user,
                **self.sale_kwargs(company=company, product=product),
            )
        self.assertFalse(Sale.objects.exists())
        self.assertFalse(ProviderJob.objects.exists())

    def test_invalid_sale_creates_no_job(self):
        with self.assertRaises(ValueError):
            self.charge(payment_method=None)
        self.assertFalse(Sale.objects.exists())
        self.assertFalse(ProviderJob.objects.exists())
        self.assertEqual(self.balance(), START)


class RunChargeJobTests(ChargingTestBase):
    def test_run_charge_job_twice_charges_once(self):
        sale = self.charge()
        with self.assertLogs("sales.tasks", level="INFO"):
            run_charge_job(str(sale.charge_job_id))
        self.assertEqual(len(self.fake.charges), 1)

    def test_refuses_cancelled_sale_without_calling_provider(self):
        sale = self.charge_without_worker()
        Sale.objects.filter(pk=sale.pk).update(status=Sale.Status.CANCELLED)
        with self.assertLogs("sales.tasks", level="WARNING"):
            run_charge_job(str(sale.charge_job_id))
        self.assertEqual(self.fake.charges, [])
        job = self.job(sale)
        self.assertEqual(job.status, ProviderJob.STATUS_FAILED)
        self.assertEqual(job.result, {"error": "sale_cancelled"})

    def test_unknown_provider_is_failed_not_sent_and_reverses(self):
        sale = self.charge_without_worker()
        self.set_job(sale, provider_key="nope")
        run_charge_job(str(sale.charge_job_id))
        self.assertFalse(Sale.objects.filter(pk=sale.pk).exists())
        self.assertFalse(ProviderOperation.objects.exists())
        self.assertEqual(self.balance(), START)
        job = self.job(sale)
        self.assertEqual(job.status, ProviderJob.STATUS_DONE)
        self.assertTrue(job.result.get("discarded"))

    def test_context_error_never_leaves_job_running(self):
        sale = self.charge_without_worker()
        with patch(
            "sales.tasks.charging.charge_context", side_effect=RuntimeError("bad data")
        ), self.assertLogs("sales.tasks", level="ERROR"):
            run_charge_job(str(sale.charge_job_id))
        self.assertEqual(self.fake.charges, [])
        self.assertFalse(Sale.objects.filter(pk=sale.pk).exists())
        self.assertFalse(ProviderOperation.objects.exists())
        self.assertEqual(self.job(sale).status, ProviderJob.STATUS_DONE)
        self.assertTrue(self.job(sale).result.get("discarded"))

    def test_apply_outcome_error_fails_job_and_leaves_sale_open(self):
        sale = self.charge_without_worker()
        with patch(
            "sales.tasks.charging.apply_outcome", side_effect=RuntimeError("db gone")
        ), self.assertLogs("sales.tasks", level="ERROR"):
            run_charge_job(str(sale.charge_job_id))
        self.assertEqual(len(self.fake.charges), 1)
        sale.refresh_from_db()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.PROCESSING)
        job = self.job(sale)
        self.assertEqual(job.status, ProviderJob.STATUS_FAILED)
        self.assertEqual(job.result["result"], "success")


class IdempotencyTests(ChargingTestBase):
    def test_duplicate_form_token_returns_existing_sale(self):
        first = self.charge("same")
        second = self.charge("same")
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Sale.objects.count(), 1)
        self.assertEqual(ProviderJob.objects.count(), 1)
        self.assertEqual(len(self.fake.charges), 1)
        self.assertEqual(self.balance(), START - COST)

    def test_concurrent_duplicate_rolls_back_and_returns_existing_sale(self):
        first = self.charge("race")
        real = charging._job_for_token
        calls = []

        def racing_lookup(token):
            calls.append(token)
            # The pre-check misses the job the other request committed meanwhile.
            return None if len(calls) == 1 else real(token)

        with patch("sales.charging._job_for_token", side_effect=racing_lookup):
            second, outcome = self.charge_with_outcome("race")
        self.assertEqual(len(calls), 2)
        self.assertEqual(second.pk, first.pk)
        self.assertEqual(outcome, "duplicate")
        self.assertEqual(Sale.objects.count(), 1)
        self.assertEqual(ProviderJob.objects.count(), 1)
        self.assertEqual(CompanyBalanceTransaction.objects.count(), 1)
        self.assertEqual(self.balance(), START - COST)
        self.assertEqual(len(self.fake.charges), 1)


class ApplyOutcomeTests(ChargingTestBase):
    def test_final_success_ignores_later_failed(self):
        sale = self.charge()
        with self.assertLogs("sales.charging", level="WARNING"):
            charging.apply_outcome(sale.pk, ChargeOutcome(result=ChargeResult.FAILED))
        sale.refresh_from_db()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.SUCCESS)
        self.assertEqual(sale.status, Sale.Status.PENDING)
        self.assertEqual(self.reversals(sale), 0)

    def test_success_after_reversal_is_flagged_not_undone(self):
        self.use(result=ChargeResult.FAILED)
        sale = self.charge()
        with self.assertLogs("sales.charging", level="ERROR"):
            charging.apply_outcome(
                sale.pk, ChargeOutcome(result=ChargeResult.SUCCESS, provider_ref="LATE-1")
            )
        sale.refresh_from_db()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.FAILED)
        self.assertEqual(sale.status, Sale.Status.CANCELLED)
        self.assertTrue(sale.charge_conflict)
        self.assertEqual(self.reversals(sale), 1)
        self.assertEqual(self.balance(), START)
        log = AuditLog.objects.get(
            object_id=str(sale.pk), changes___extra__charge_conflict="success_after_reversal"
        )
        self.assertEqual(log.changes["_extra"]["provider_ref"], "LATE-1")

    def test_success_on_open_but_cancelled_sale_is_flagged(self):
        sale = self.unverified()
        Sale.objects.filter(pk=sale.pk).update(status=Sale.Status.CANCELLED)
        with self.assertLogs("sales.charging", level="ERROR"):
            charging.apply_outcome(sale.pk, ChargeOutcome(result=ChargeResult.SUCCESS))
        sale.refresh_from_db()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.SUCCESS)
        self.assertEqual(sale.status, Sale.Status.CANCELLED)
        self.assertTrue(sale.charge_conflict)
        self.assertEqual(self.deductions(sale), 1)

    def test_failed_on_already_cancelled_sale_adds_no_reversal(self):
        sale = self.unverified()
        Sale.objects.filter(pk=sale.pk).update(status=Sale.Status.CANCELLED)
        charging.apply_outcome(sale.pk, ChargeOutcome(result=ChargeResult.FAILED))
        sale.refresh_from_db()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.FAILED)
        self.assertEqual(self.reversals(sale), 0)


class GuardTests(ChargingTestBase):
    """Existing cancel / edit / delete flows must not bypass the charge state."""

    def edit(self, sale, **overrides):
        kwargs = dict(
            sale=sale,
            payment_method=self.pm,
            payer_name=sale.payer_name,
            reference_number=sale.reference_number,
            sell_price_actual=sale.sell_price_actual,
            notes="",
            user=self.manager,
        )
        kwargs.update(overrides)
        return update_sale_fields(**kwargs)

    def test_cancel_sale_rejects_open_charge(self):
        sale = self.unverified()
        with self.assertRaises(ValueError):
            cancel_sale(sale=sale, user=self.manager)
        sale.refresh_from_db()
        self.assertEqual(sale.status, Sale.Status.PENDING)
        self.assertEqual(self.reversals(sale), 0)

    def test_cancel_allowed_once_charge_succeeded(self):
        sale = self.charge()
        cancel_sale(sale=sale, user=self.manager)
        self.assertEqual(self.reversals(sale), 1)

    def test_management_cancel_view_shows_error_for_open_charge(self):
        sale = self.unverified()
        self.client.force_login(self.manager)
        response = self.client.post(reverse("sales:sale_cancel", args=[sale.pk]), follow=True)
        self.assertContains(response, "still being charged")
        sale.refresh_from_db()
        self.assertEqual(sale.status, Sale.Status.PENDING)
        self.assertEqual(self.reversals(sale), 0)

    def test_update_rejects_number_or_price_change_on_open_charge(self):
        sale = self.unverified()
        with self.assertRaises(ValueError):
            self.edit(sale, reference_number="0599000000")
        with self.assertRaises(ValueError):
            self.edit(sale, sell_price_actual=Decimal("25"))
        sale.refresh_from_db()
        self.assertEqual(sale.reference_number, "0599123456")
        self.edit(sale, payer_name="Someone")
        sale.refresh_from_db()
        self.assertEqual(sale.payer_name, "Someone")

    def test_management_edit_view_shows_error_for_number_change(self):
        sale = self.unverified()
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("sales:sale_edit", args=[sale.pk]),
            {
                "payment_method": self.pm.pk,
                "payer_name": "Walk-in",
                "reference_number": "0501234567",
                "sell_price_actual": "20",
                "notes": "",
            },
        )
        self.assertContains(response, "still being charged")
        sale.refresh_from_db()
        self.assertEqual(sale.reference_number, "0599123456")

    def test_delete_rejects_auto_charge_unless_failed(self):
        sale = self.charge()
        with self.assertRaises(ValueError):
            delete_sale_permanently(sale=sale, user=self.manager)
        self.assertTrue(Sale.objects.filter(pk=sale.pk).exists())

        self.use(result=ChargeResult.FAILED)
        failed = self.charge("t-failed")
        delete_sale_permanently(sale=failed, user=self.manager)
        self.assertFalse(Sale.objects.filter(pk=failed.pk).exists())

    def test_management_delete_view_shows_error(self):
        sale = self.unverified()
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("sales:sale_delete_permanent", args=[sale.pk]), follow=True
        )
        self.assertContains(response, "Could not delete this sale")
        self.assertTrue(Sale.objects.filter(pk=sale.pk).exists())
        self.assertEqual(self.balance(), START - COST)

    def test_employee_cannot_modify_auto_charge_sale(self):
        sale = self.charge()
        self.assertFalse(sale.is_employee_modifiable)
        self.assertFalse(sale_is_employee_editable(sale, self.user))
        self.assertNotIn(sale.pk, employee_editable_sale_ids(self.user))

        self.client.force_login(self.user)
        response = self.client.post(
            reverse("sales:employee_sale_delete", args=[sale.pk]), follow=True
        )
        self.assertContains(response, "charged through the provider")
        self.assertNotContains(response, "You can only edit or delete your last")
        self.assertTrue(Sale.objects.filter(pk=sale.pk).exists())
        self.assertEqual(self.balance(), START - COST)

        response = self.client.get(
            reverse("sales:employee_sale_edit", args=[sale.pk]), follow=True
        )
        self.assertContains(response, "charged through the provider")

    def test_phone_line_rename_refused_while_any_charge_is_open(self):
        from reports.models import PhoneLineTracking
        from reports.stale_phones import rename_phone_line

        open_sale = self.unverified()
        self.use()
        done_sale = self.charge("t-done")
        PhoneLineTracking.objects.create(reference_key="0599123456", notes="old")
        with self.assertRaisesMessage(ValueError, "charge still in progress"):
            rename_phone_line(old_key="0599123456", new_reference="0501234567", notes="new")
        open_sale.refresh_from_db()
        done_sale.refresh_from_db()
        self.assertEqual(open_sale.reference_number, "0599123456")
        self.assertEqual(done_sale.reference_number, "0599123456")
        self.assertEqual(
            list(PhoneLineTracking.objects.values_list("reference_key", "notes")),
            [("0599123456", "old")],
        )

    def test_phone_line_rename_allowed_once_charge_settled(self):
        from reports.stale_phones import rename_phone_line

        sale = self.charge()
        self.assertEqual(
            rename_phone_line(old_key="0599123456", new_reference="0501234567"),
            "0501234567",
        )
        sale.refresh_from_db()
        self.assertEqual(sale.reference_number, "0501234567")


class ManualResolveTests(ChargingTestBase):
    def test_manual_success_keeps_ref_and_audits_user(self):
        sale = self.unverified(provider_ref="R-9")
        self.assertEqual(sale.charge_provider_ref, "")
        Sale.objects.filter(pk=sale.pk).update(charge_provider_ref="R-9")
        sale = charging.manual_resolve(sale, success=True, user=self.manager)
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.SUCCESS)
        self.assertEqual(sale.charge_provider_ref, "R-9")
        self.assertEqual(self.balance(), START - COST)
        log = AuditLog.objects.get(
            object_id=str(sale.pk),
            actor=self.manager,
            changes___extra__charge_status="success",
        )
        self.assertEqual(log.changes["_extra"]["previous_charge_message"], "الشحن قيد المتابعة")

    def test_manual_failure_reverses_once(self):
        sale = self.unverified()
        sale = charging.manual_resolve(sale, success=False, user=self.manager)
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.FAILED)
        self.assertEqual(sale.status, Sale.Status.CANCELLED)
        self.assertEqual(sale.cancelled_by, self.manager)
        self.assertEqual(self.reversals(sale), 1)
        self.assertEqual(self.balance(), START)

    def test_manual_resolve_rejects_final_and_manual_sales(self):
        sale = self.charge()
        with self.assertRaises(ValueError):
            charging.manual_resolve(sale, success=False, user=self.manager)
        Sale.objects.filter(pk=sale.pk).update(
            charge_status="", entry_mode=Sale.EntryMode.MANUAL
        )
        sale.refresh_from_db()
        with self.assertRaises(ValueError):
            charging.manual_resolve(sale, success=True, user=self.manager)
        self.assertEqual(self.reversals(sale), 0)

    def test_manual_resolve_on_queued_job_stops_the_worker(self):
        sale = self.charge_without_worker()
        charging.manual_resolve(sale, success=False, user=self.manager)
        self.assertEqual(self.job(sale).status, ProviderJob.STATUS_FAILED)
        with self.assertLogs("sales.tasks", level="INFO"):
            run_charge_job(str(sale.charge_job_id))
        self.assertEqual(self.fake.charges, [])
        self.assertEqual(self.reversals(sale), 1)

    def test_manual_success_rejected_when_charge_never_sent(self):
        sale = self.charge_without_worker()
        with self.assertRaises(charging.ChargeNotSent):
            charging.manual_resolve(sale, success=True, user=self.manager)
        sale.refresh_from_db()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.PROCESSING)
        self.assertEqual(self.job(sale).status, ProviderJob.STATUS_QUEUED)

        sale = charging.manual_resolve(sale, success=False, user=self.manager)
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.FAILED)
        self.assertEqual(self.balance(), START)

    def test_manual_resolve_rejects_freshly_running_job(self):
        sale = self.charge_without_worker()
        self.set_job(
            sale,
            status=ProviderJob.STATUS_RUNNING,
            started_at=timezone.now() - dt.timedelta(minutes=2),
        )
        with self.assertRaises(charging.ChargeInProgress):
            charging.manual_resolve(sale, success=False, user=self.manager)
        sale.refresh_from_db()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.PROCESSING)
        self.assertEqual(self.reversals(sale), 0)

    def test_manual_resolve_allowed_on_stale_running_job(self):
        sale = self.charge_without_worker()
        self.set_job(
            sale,
            status=ProviderJob.STATUS_RUNNING,
            started_at=timezone.now() - dt.timedelta(minutes=11),
        )
        sale = charging.manual_resolve(sale, success=True, user=self.manager)
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.SUCCESS)


class VerifyTaskTests(ChargingTestBase):
    def unverified_due(self, **options) -> Sale:
        """An unverified sale whose last check is old enough to re-check."""
        sale = self.unverified(**options)
        self.checked_ago(sale, 6)
        return sale

    def test_verify_task_resolves_unverified_to_success(self):
        sale = self.unverified_due(check_result=ChargeResult.SUCCESS, provider_ref="R-5")
        verify_unverified_charges()
        sale.refresh_from_db()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.SUCCESS)
        self.assertEqual(sale.charge_provider_ref, "R-5")
        self.assertEqual(len(self.fake.charges), 1)
        self.assertEqual(len(self.fake.checks), 1)
        self.assertEqual(self.balance(), START - COST)

    def test_verify_task_failed_check_reverses_once(self):
        sale = self.unverified_due(check_result=ChargeResult.FAILED)
        verify_unverified_charges()
        verify_unverified_charges()
        sale.refresh_from_db()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.FAILED)
        self.assertEqual(sale.status, Sale.Status.CANCELLED)
        self.assertEqual(self.reversals(sale), 1)
        self.assertEqual(len(self.fake.checks), 1)

    def test_verify_task_still_unverified_leaves_sale(self):
        sale = self.unverified_due(check_result=ChargeResult.UNVERIFIED)
        verify_unverified_charges()
        sale.refresh_from_db()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.UNVERIFIED)
        self.assertEqual(sale.status, Sale.Status.PENDING)
        self.assertEqual(self.reversals(sale), 0)
        self.assertEqual(len(self.fake.charges), 1)

    def test_verify_task_skips_recently_checked_sale(self):
        sale = self.unverified(check_result=ChargeResult.SUCCESS)
        self.checked_ago(sale, 4)
        verify_unverified_charges()
        sale.refresh_from_db()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.UNVERIFIED)
        self.assertEqual(self.fake.checks, [])

    def test_verify_task_skips_provider_without_check_capability(self):
        sale = self.unverified_due(capabilities=frozenset({Capability.CHARGE}))
        verify_unverified_charges()
        self.assertEqual(self.fake.checks, [])
        sale.refresh_from_db()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.UNVERIFIED)

    def test_verify_task_is_single_flight(self):
        sale = self.unverified_due(check_result=ChargeResult.SUCCESS)
        get_redis().set("sales:verify-charges:lock", "other", ex=60)
        verify_unverified_charges()
        self.assertEqual(self.fake.checks, [])
        get_redis().delete("sales:verify-charges:lock")
        verify_unverified_charges()
        sale.refresh_from_db()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.SUCCESS)
        self.assertFalse(get_redis().exists("sales:verify-charges:lock"))

    def test_verify_lock_release_is_token_checked_and_atomic(self):
        from providers.sessions import NoLuaLock

        def lose_lock():
            get_redis().set("sales:verify-charges:lock", "next-run", ex=60)

        with patch("sales.tasks._verify_due_sales", side_effect=lose_lock), patch.object(
            NoLuaLock, "do_release", autospec=True, side_effect=NoLuaLock.do_release
        ) as release, self.assertLogs("sales.tasks", level="WARNING"):
            verify_unverified_charges()
        release.assert_called_once()
        self.assertEqual(get_redis().get("sales:verify-charges:lock"), b"next-run")

    def test_verify_task_stops_when_run_budget_is_spent(self):
        self.use(raise_after_send=TransientError("timeout"), check_result=ChargeResult.SUCCESS)
        with self.assertLogs("providers.service", level="WARNING"):
            first = self.charge("tok-1")
            second = self.charge("tok-2")
        self.age(first, 20)
        self.checked_ago(first, 6)
        self.checked_ago(second, 6)
        clock = itertools.chain([0.0, 0.0], itertools.repeat(10_000.0))
        with patch("sales.tasks.monotonic", side_effect=lambda: next(clock)):
            verify_unverified_charges()
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.charge_status, Sale.ChargeStatus.SUCCESS)
        self.assertEqual(second.charge_status, Sale.ChargeStatus.UNVERIFIED)
        self.assertEqual(len(self.fake.checks), 1)

    def test_verify_task_requeues_never_run_job(self):
        sale = self.charge_without_worker()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.PROCESSING)
        verify_unverified_charges()
        self.assertEqual(self.fake.charges, [])

        self.age(sale, 6)
        verify_unverified_charges()
        sale.refresh_from_db()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.SUCCESS)
        self.assertEqual(len(self.fake.charges), 1)
        self.assertEqual(self.fake.checks, [])

    def test_verify_task_skips_recently_started_job(self):
        sale = self.charge_without_worker()
        self.set_job(
            sale,
            status=ProviderJob.STATUS_RUNNING,
            started_at=timezone.now() - dt.timedelta(minutes=9),
        )
        self.age(sale, 9)
        verify_unverified_charges()
        sale.refresh_from_db()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.PROCESSING)
        self.assertEqual(self.fake.charges, [])
        self.assertEqual(self.fake.checks, [])

    def test_verify_task_checks_stale_running_job_without_charging(self):
        sale = self.charge_without_worker()
        self.use(check_result=ChargeResult.FAILED)
        self.set_job(
            sale,
            status=ProviderJob.STATUS_RUNNING,
            started_at=timezone.now() - dt.timedelta(minutes=11),
        )
        self.age(sale, 11)
        verify_unverified_charges()
        sale.refresh_from_db()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.FAILED)
        self.assertEqual(self.fake.charges, [])
        self.assertEqual(len(self.fake.checks), 1)
        self.assertEqual(self.balance(), START)

    def test_recheck_runs_a_check_for_one_sale(self):
        sale = self.unverified(check_result=ChargeResult.SUCCESS)
        charging.recheck(sale)
        sale.refresh_from_db()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.SUCCESS)
        self.assertEqual(len(self.fake.charges), 1)
        self.assertEqual(sale.charge_message, "تمّت متابعة الشحن — رقم العملية FAKE-REF-1")
        rows = ProviderOperation.objects.filter(service="check_charge")
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.get().job_id, sale.charge_job_id)
        self.assertEqual(rows.get().source, "system")

    def test_check_sale_charge_ignores_final_sale(self):
        sale = self.charge()
        check_sale_charge(sale.pk)
        self.assertEqual(self.fake.checks, [])

    def test_beat_runs_verify_every_two_minutes(self):
        entry = settings.CELERY_BEAT_SCHEDULE["sales-verify-charges"]
        self.assertEqual(entry["task"], "sales.tasks.verify_unverified_charges")
        self.assertEqual(entry["schedule"], 120.0)


class CanChargeTests(ChargingTestBase):
    def test_can_charge_false_for_refresh_only_provider(self):
        for key in ("sky", ""):
            company = Company(name="x", provider_key=key)
            self.assertFalse(charging.can_charge(company))

    def test_can_charge_true_for_no_login_adapter_without_account(self):
        self.assertTrue(charging.can_charge(self.company))

    def test_login_adapter_needs_enabled_account(self):
        self.use(needs_login=True)
        # The seeded row has no credentials: not an account.
        self.assertFalse(charging.can_charge(self.company))
        account = _save_credentials("fake")
        self.assertTrue(charging.can_charge(self.company))
        account.is_enabled = False
        account.save()
        self.assertFalse(charging.can_charge(self.company))

    def test_login_adapter_with_undecryptable_credentials_cannot_charge(self):
        self.use(needs_login=True)
        account = _save_credentials("fake")
        account.secret_encrypted = "garbage"
        account.save()
        self.assertFalse(charging.can_charge(self.company))

    def test_can_charge_needs_charge_switch(self):
        connection = ProviderConnection.for_key("fake")
        connection.charge_enabled = False
        connection.save()
        self.assertFalse(charging.can_charge(self.company))
        connection.charge_enabled = True
        connection.save()
        self.assertTrue(charging.can_charge(self.company))
