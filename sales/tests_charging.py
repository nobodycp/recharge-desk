"""Charge-and-record-sale: money safety around the provider charge."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from audit.models import AuditLog
from companies.models import Company, Product, ProductLine
from providers.contracts import (
    AuthRequired,
    ChargeOutcome,
    ChargeResult,
    TransientError,
)
from providers.models import ProviderAccount, ProviderJob
from providers.redis_conn import get_redis
from providers.registry import ADAPTERS
from providers.tests.fakes import FakeChargeAdapter
from sales import charging
from sales.models import CompanyBalanceTransaction, PaymentMethod, Sale
from sales.tasks import check_sale_charge, run_charge_job, verify_unverified_charges

User = get_user_model()
START = Decimal("1000.00")
COST = Decimal("10.00")


class ChargingTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("cashier", password="x")
        cls.manager = User.objects.create_user("boss", password="x")
        cls.company = Company.objects.create(
            name="FakeCo", phone_refresh_provider="fake", current_balance=START
        )
        line = ProductLine.objects.create(company=cls.company, name="Line")
        cls.product = Product.objects.create(
            line=line, cost_price=COST, default_sell_price=Decimal("20")
        )
        cls.pm = PaymentMethod.objects.create(name="Cash")

    def setUp(self):
        get_redis().flushall()
        self.use()

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

    def charge(self, token="tok-1", **overrides) -> Sale:
        with self.captureOnCommitCallbacks(execute=True):
            sale = charging.create_charged_sale(
                form_token=token, user=self.user, **self.sale_kwargs(**overrides)
            )
        sale.refresh_from_db()
        return sale

    def charge_without_worker(self, token="tok-1") -> Sale:
        """Create the sale while the broker is down: the job stays queued."""
        with patch(
            "sales.tasks.run_charge_job.delay", side_effect=ConnectionError("broker down")
        ), self.assertLogs("sales.charging", level="ERROR"):
            sale = self.charge(token)
        return sale

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
        self.assertEqual(sale.status, Sale.Status.PENDING)
        self.assertEqual(self.balance(), START - COST)
        self.assertEqual(self.deductions(sale), 1)
        self.assertEqual(self.reversals(sale), 0)

        job = ProviderJob.objects.get(pk=sale.charge_job_id)
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
        self.assertTrue(
            AuditLog.objects.filter(
                object_id=str(sale.pk), changes___extra__charge_status="success"
            ).exists()
        )

    def test_failed_cancels_sale_and_restores_balance_once(self):
        self.use(result=ChargeResult.FAILED, message="line not found")
        sale = self.charge()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.FAILED)
        self.assertEqual(sale.status, Sale.Status.CANCELLED)
        self.assertEqual(sale.charge_message, "line not found")
        self.assertEqual(self.balance(), START)
        self.assertEqual(self.reversals(sale), 1)

        with self.assertLogs("sales.charging", level="INFO"):
            charging.apply_outcome(
                sale.pk, ChargeOutcome(result=ChargeResult.FAILED), actor=self.manager
            )
        self.assertEqual(self.reversals(sale), 1)
        self.assertEqual(self.balance(), START)

    def test_transient_after_send_is_unverified_without_reversal(self):
        self.use(raise_after_send=TransientError("timeout"))
        with self.assertLogs("providers.service", level="WARNING"):
            sale = self.charge()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.UNVERIFIED)
        self.assertEqual(sale.status, Sale.Status.PENDING)
        self.assertEqual(self.balance(), START - COST)
        self.assertEqual(self.reversals(sale), 0)
        self.assertEqual(self.fake.sent, 1)

    def test_charge_never_sent_because_login_failed_reverses(self):
        ProviderAccount.objects.create(provider_key="fake", is_enabled=True)
        self.use(
            needs_login=True,
            raise_before_send=AuthRequired("expired"),
            login_error=RuntimeError("otp rejected"),
        )
        with patch("providers.sessions.accounts.record_login"), self.assertLogs(
            "providers", level="WARNING"
        ):
            sale = self.charge()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.FAILED)
        self.assertEqual(sale.charge_message, "not_sent:login_failed")
        self.assertEqual(sale.status, Sale.Status.CANCELLED)
        self.assertEqual(self.balance(), START)
        self.assertEqual(self.fake.sent, 0)

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
        company = Company.objects.create(name="SkyCo", phone_refresh_provider="sky")
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
            second = self.charge("race")
        self.assertEqual(len(calls), 2)
        self.assertEqual(second.pk, first.pk)
        self.assertEqual(Sale.objects.count(), 1)
        self.assertEqual(ProviderJob.objects.count(), 1)
        self.assertEqual(CompanyBalanceTransaction.objects.count(), 1)
        self.assertEqual(self.balance(), START - COST)
        self.assertEqual(len(self.fake.charges), 1)

    def test_run_charge_job_twice_charges_once(self):
        sale = self.charge()
        with self.assertLogs("sales.tasks", level="INFO"):
            run_charge_job(str(sale.charge_job_id))
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

    def test_failed_on_already_cancelled_sale_adds_no_reversal(self):
        from sales.services import cancel_sale

        self.use(raise_after_send=TransientError("timeout"))
        with self.assertLogs("providers.service", level="WARNING"):
            sale = self.charge()
        cancel_sale(sale=sale, user=self.manager)
        charging.apply_outcome(sale.pk, ChargeOutcome(result=ChargeResult.FAILED))
        sale.refresh_from_db()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.FAILED)
        self.assertEqual(self.reversals(sale), 1)
        self.assertEqual(self.balance(), START)


class ManualResolveTests(ChargingTestBase):
    def unverified_sale(self) -> Sale:
        self.use(raise_after_send=TransientError("timeout"), provider_ref="R-9")
        with self.assertLogs("providers.service", level="WARNING"):
            return self.charge()

    def test_manual_success_keeps_ref_and_audits_user(self):
        sale = self.unverified_sale()
        Sale.objects.filter(pk=sale.pk).update(charge_provider_ref="R-9")
        sale = charging.manual_resolve(sale, success=True, user=self.manager)
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.SUCCESS)
        self.assertEqual(sale.charge_provider_ref, "R-9")
        self.assertEqual(self.balance(), START - COST)
        self.assertTrue(
            AuditLog.objects.filter(
                object_id=str(sale.pk),
                actor=self.manager,
                changes___extra__charge_status="success",
            ).exists()
        )

    def test_manual_failure_reverses_once(self):
        sale = self.unverified_sale()
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

    def test_job_skips_charge_when_sale_resolved_before_it_ran(self):
        sale = self.charge_without_worker()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.PROCESSING)
        charging.manual_resolve(sale, success=False, user=self.manager)
        with self.assertLogs("sales.tasks", level="WARNING"):
            run_charge_job(str(sale.charge_job_id))
        self.assertEqual(self.fake.charges, [])
        job = ProviderJob.objects.get(pk=sale.charge_job_id)
        self.assertEqual(job.status, ProviderJob.STATUS_FAILED)


class VerifyTaskTests(ChargingTestBase):
    def unverified_sale(self, **options) -> Sale:
        self.use(raise_after_send=TransientError("timeout"), **options)
        with self.assertLogs("providers.service", level="WARNING"):
            return self.charge()

    def test_verify_task_resolves_unverified_to_success(self):
        sale = self.unverified_sale(check_result=ChargeResult.SUCCESS, provider_ref="R-5")
        verify_unverified_charges()
        sale.refresh_from_db()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.SUCCESS)
        self.assertEqual(sale.charge_provider_ref, "R-5")
        self.assertEqual(len(self.fake.charges), 1)
        self.assertEqual(len(self.fake.checks), 1)
        self.assertEqual(self.balance(), START - COST)

    def test_verify_task_failed_check_reverses_once(self):
        sale = self.unverified_sale(check_result=ChargeResult.FAILED)
        verify_unverified_charges()
        verify_unverified_charges()
        sale.refresh_from_db()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.FAILED)
        self.assertEqual(sale.status, Sale.Status.CANCELLED)
        self.assertEqual(self.reversals(sale), 1)
        self.assertEqual(len(self.fake.checks), 1)

    def test_verify_task_still_unverified_leaves_sale(self):
        sale = self.unverified_sale(check_result=ChargeResult.UNVERIFIED)
        verify_unverified_charges()
        sale.refresh_from_db()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.UNVERIFIED)
        self.assertEqual(sale.status, Sale.Status.PENDING)
        self.assertEqual(self.reversals(sale), 0)
        self.assertEqual(len(self.fake.charges), 1)

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
        ProviderJob.objects.filter(pk=sale.charge_job_id).update(
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
        ProviderJob.objects.filter(pk=sale.charge_job_id).update(
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
        sale = self.unverified_sale(check_result=ChargeResult.SUCCESS)
        charging.recheck(sale)
        sale.refresh_from_db()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.SUCCESS)
        self.assertEqual(len(self.fake.charges), 1)

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
            company = Company(name="x", phone_refresh_provider=key)
            self.assertFalse(charging.can_charge(company))

    def test_can_charge_true_for_no_login_adapter_without_account(self):
        self.assertTrue(charging.can_charge(self.company))

    def test_login_adapter_needs_enabled_account(self):
        self.use(needs_login=True)
        self.assertFalse(charging.can_charge(self.company))
        account = ProviderAccount.objects.create(provider_key="fake", is_enabled=False)
        self.assertFalse(charging.can_charge(self.company))
        account.is_enabled = True
        account.save()
        self.assertTrue(charging.can_charge(self.company))
