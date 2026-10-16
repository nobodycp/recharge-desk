"""Charge-and-record sale: entry button, status badges, management filters and actions."""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone

from companies.models import Company
from customers.models import Customer
from sales.models import Sale
from sales.services import create_sale
from sales.tests_charging import START, ChargingTestBase
from sales.views._shared import RECHECK_POLLS


class ChargeUiTestBase(ChargingTestBase):
    def entry_payload(self, **overrides):
        data = {
            "company": self.company.pk,
            "product": self.product.pk,
            "reference_number": "0501234567",
            "payer_name": "Walk-in",
            "sell_price_actual": "20",
            "payment_method": self.pm.pk,
            "form_token": "11111111-1111-4111-8111-111111111111",
        }
        data.update(overrides)
        return data

    def post_entry(self, **overrides):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(reverse("sales:employee_entry"), self.entry_payload(**overrides))

    def manual_sale(self, *, user=None, reference="0599000001", **charge_fields) -> Sale:
        sale = create_sale(user=user or self.user, **self.sale_kwargs(reference_number=reference))
        if charge_fields:
            charge_fields.setdefault("entry_mode", Sale.EntryMode.AUTO_CHARGE)
            Sale.objects.filter(pk=sale.pk).update(**charge_fields)
            sale.refresh_from_db()
        return sale

    def assert_charge_chip(self, resp, status, label):
        """The badge's state segment for ``status`` with its own label (not a substring elsewhere)."""
        self.assertContains(resp, f"rd-charge-tag--{status}")
        self.assertContains(resp, f"{label}</span>")

    def assert_polls(self, resp):
        self.assertContains(resp, 'hx-trigger="every 5s [')
        self.assertContains(resp, "data-rd-poll")


class EmployeeEntryChargeButtonTests(ChargeUiTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_charge_button_hidden_when_company_cannot_charge(self):
        Company.objects.filter(pk=self.company.pk).update(provider_key="")
        resp = self.client.get(reverse("sales:employee_entry"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'value="charge"')
        self.assertNotContains(resp, 'data-can-charge="')
        self.assertContains(resp, 'name="form_token"')

    def test_charge_button_hidden_until_management_grants_it(self):
        self.user.profile.can_provider_charge = False
        self.user.profile.save(update_fields=["can_provider_charge"])
        resp = self.client.get(reverse("sales:employee_entry"))
        self.assertNotContains(resp, 'value="charge"')
        posted = self.post_entry(action="charge")
        self.assertEqual(Sale.objects.count(), 0)
        self.assertEqual(posted.status_code, 200)
        self.assertContains(posted, "This company cannot be charged through its provider.")

    def test_charge_button_shown_for_fake_provider(self):
        other = Company.objects.create(name="PlainCo", current_balance=Decimal("100"))
        resp = self.client.get(reverse("sales:employee_entry"))
        self.assertContains(resp, 'name="action" value="charge"')
        html = resp.content.decode()
        self.assertRegex(html, rf'data-company-id="{self.company.pk}"\s+data-can-charge="1"')
        self.assertRegex(html, rf'data-company-id="{other.pk}"\s+data-can-charge="0"')
        self.assertContains(resp, 'name="form_token"')

    def test_form_token_is_new_per_render(self):
        first = self.client.get(reverse("sales:employee_entry")).context["form_token"]
        second = self.client.get(reverse("sales:employee_entry")).context["form_token"]
        self.assertTrue(first)
        self.assertNotEqual(first, second)

    def test_charge_submit_creates_auto_charge_sale(self):
        resp = self.post_entry(action="charge")
        self.assertRedirects(resp, reverse("sales:employee_entry"), fetch_redirect_response=False)
        sale = Sale.objects.get()
        self.assertEqual(sale.entry_mode, Sale.EntryMode.AUTO_CHARGE)
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.SUCCESS)
        self.assertEqual(sale.created_by, self.user)
        self.assertEqual(len(self.fake.charges), 1)
        self.assertEqual(self.balance(), Decimal("990.00"))

    def test_charge_resubmit_same_token_returns_existing_sale(self):
        self.post_entry(action="charge")
        resp = self.post_entry(action="charge")
        self.assertEqual(Sale.objects.count(), 1)
        self.assertEqual(len(self.fake.charges), 1)
        self.assertEqual(self.balance(), Decimal("990.00"))
        page = self.client.get(resp["Location"])
        self.assertContains(page, "This sale was already recorded")

    def test_token_used_by_another_user_is_a_clear_error(self):
        self.post_entry(action="charge")
        first = Sale.objects.get()
        self.client.force_login(self.manager)
        resp = self.post_entry(action="charge")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "This form was already submitted.")
        self.assertNotContains(resp, "This sale was already recorded")
        self.assertEqual(list(Sale.objects.all()), [first])
        self.assertEqual(len(self.fake.charges), 1)

    def test_rejected_charge_does_not_create_customer(self):
        payload = {"action": "charge", "on_account": "on", "payment_method": ""}
        Company.objects.filter(pk=self.company.pk).update(provider_key="")
        resp = self.post_entry(**payload)
        self.assertContains(resp, "This company cannot be charged through its provider.")
        self.assertFalse(Customer.objects.exists())

    def test_duplicate_charge_does_not_touch_customers(self):
        self.post_entry(action="charge", on_account="on", payment_method="")
        self.assertEqual(Customer.objects.count(), 1)
        with patch("sales.views.employee.resolve_or_create_customer_for_sale") as resolve:
            resp = self.post_entry(action="charge", on_account="on", payment_method="")
        resolve.assert_not_called()
        self.assertRedirects(resp, reverse("sales:employee_entry"), fetch_redirect_response=False)
        self.assertEqual(Sale.objects.count(), 1)

    def test_charge_submit_rejected_when_company_cannot_charge(self):
        Company.objects.filter(pk=self.company.pk).update(provider_key="")
        resp = self.post_entry(action="charge")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "This company cannot be charged through its provider.")
        self.assertFalse(Sale.objects.exists())

    def test_second_sale_same_day_needs_confirmation(self):
        self.post_entry()
        blocked = self.post_entry(form_token="22222222-2222-4222-8222-222222222222")
        self.assertEqual(blocked.status_code, 200)
        self.assertContains(blocked, "تم شحن هذا الرقم اليوم")
        self.assertEqual(Sale.objects.count(), 1)
        allowed = self.post_entry(
            form_token="33333333-3333-4333-8333-333333333333",
            repeat_charge="1",
        )
        self.assertRedirects(allowed, reverse("sales:employee_entry"), fetch_redirect_response=False)
        self.assertEqual(Sale.objects.count(), 2)

    def test_sale_today_reports_an_existing_charge(self):
        url = reverse("sales:employee_sale_today")
        before = self.client.get(url, {"phone": "0501234567"})
        self.assertFalse(before.json()["charged_today"])
        self.post_entry()
        after = self.client.get(url, {"phone": "0501234567"})
        self.assertTrue(after.json()["charged_today"])

    def test_a_missing_price_does_not_blame_the_phone(self):
        from django.utils import translation

        from sales.forms import EmployeeSaleForm

        translation.activate("ar")
        self.addCleanup(translation.deactivate)
        form = EmployeeSaleForm(
            data=self.entry_payload(sell_price_actual="", duration_days="x"),
            company_id=self.company.pk,
            user=self.user,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("أدخل سعر البيع", str(form.errors["sell_price_actual"]))
        self.assertIn("الأيام رقم صحيح من 1 إلى 366", str(form.errors["duration_days"]))
        self.assertNotIn("رقم الهاتف مطلوب", str(form.errors))
        translation.deactivate()

        resp = self.client.post(
            reverse("sales:employee_entry"),
            self.entry_payload(sell_price_actual="", duration_days="9"),
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        price = body.split('id="rd-sell-price-wrap"')[1].split("rd-entry-switch")[0]
        self.assertIn("has-error", price)
        self.assertNotIn('id="rd-duration-days-wrap" class="rd-field-group has-error"', body)

    def test_wrong_package_shows_the_price_mismatch(self):
        from providers.contracts import PermanentError

        self.use(raise_before_send=PermanentError("price_mismatch"))
        resp = self.post_entry(
            action="charge", form_token="22222222-2222-4222-8222-222222222222"
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Sale.objects.exists())
        page = self.client.get(reverse("sales:employee_entry"))
        self.assertContains(page, "السعر غير مطابق")
        self.client.force_login(self.manager)
        management = self.client.get(reverse("sales:management_sale_list"))
        self.assertNotContains(management, "السعر غير مطابق")

    def test_traditional_save_stays_manual(self):
        self.post_entry()
        sale = Sale.objects.get()
        self.assertEqual(sale.entry_mode, Sale.EntryMode.MANUAL)
        self.assertEqual(sale.charge_status, "")
        self.assertEqual(self.fake.charges, [])


class EmployeeRecentChargeTests(ChargeUiTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_recent_entries_show_badge_and_status(self):
        charged = self.manual_sale(
            charge_status=Sale.ChargeStatus.SUCCESS, charge_message="provider said ok-secret-detail"
        )
        failed = self.manual_sale(reference="0599000002", charge_status=Sale.ChargeStatus.FAILED)
        manual = self.manual_sale(reference="0599000003")
        resp = self.client.get(reverse("sales:employee_recent_sales"))
        self.assertContains(resp, "Auto charge", count=2)
        self.assert_charge_chip(resp, "success", "Charged")
        self.assert_charge_chip(resp, "failed", "Charge failed")
        self.assertNotContains(resp, "ok-secret-detail")
        self.assertContains(resp, f'id="sale-row-{charged.pk}"')
        self.assertContains(resp, f'id="sale-row-{failed.pk}"')
        self.assertContains(resp, f'id="sale-row-{manual.pk}"')
        self.assertNotContains(resp, "every 5s")

    def test_processing_row_polls_until_settled(self):
        sale = self.manual_sale(charge_status=Sale.ChargeStatus.PROCESSING)
        row_url = reverse("sales:sale_charge_row", args=[sale.pk])
        page = self.client.get(reverse("sales:employee_recent_sales"))
        self.assert_polls(page)
        self.assertContains(page, f'hx-get="{row_url}"')
        self.assert_charge_chip(page, "processing", "Charging")

        resp = self.client.get(row_url)
        self.assertEqual(resp.status_code, 200)
        self.assert_polls(resp)
        self.assertIn("no-cache", resp["Cache-Control"])

        Sale.objects.filter(pk=sale.pk).update(charge_status=Sale.ChargeStatus.SUCCESS)
        resp = self.client.get(row_url)
        self.assert_charge_chip(resp, "success", "Charged")
        self.assertNotContains(resp, "rd-charge-tag--processing")
        self.assertNotContains(resp, "hx-trigger")

    def test_employee_cannot_fetch_other_employees_row(self):
        other = self.manual_sale(user=self.manager, charge_status=Sale.ChargeStatus.PROCESSING)
        resp = self.client.get(reverse("sales:sale_charge_row", args=[other.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_employee_cannot_fetch_management_layout(self):
        sale = self.manual_sale(charge_status=Sale.ChargeStatus.UNVERIFIED, charge_message="raw-msg")
        resp = self.client.get(reverse("sales:sale_charge_row", args=[sale.pk]) + "?layout=row")
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "raw-msg")
        self.assertNotContains(resp, reverse("sales:sale_charge_resolve", args=[sale.pk]))


class ManagementChargeTests(ChargeUiTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.manager)

    def list_refs(self, **params):
        resp = self.client.get(reverse("sales:management_sale_list"), params)
        self.assertEqual(resp.status_code, 200)
        return {s.reference_number for s in resp.context["page_obj"]}

    def test_management_filter_by_charge_status(self):
        old = timezone.now() - dt.timedelta(minutes=45)
        self.manual_sale(reference="0599000001")
        self.manual_sale(reference="0599000002", charge_status=Sale.ChargeStatus.SUCCESS)
        self.manual_sale(reference="0599000003", charge_status=Sale.ChargeStatus.FAILED)
        self.manual_sale(reference="0599000004", charge_status=Sale.ChargeStatus.UNVERIFIED)
        self.manual_sale(
            reference="0599000005", charge_status=Sale.ChargeStatus.UNVERIFIED, created_at=old
        )
        self.manual_sale(
            reference="0599000006", charge_status=Sale.ChargeStatus.PROCESSING, created_at=old
        )
        self.manual_sale(reference="0599000007", charge_status=Sale.ChargeStatus.PROCESSING)
        self.manual_sale(
            reference="0599000008", charge_status=Sale.ChargeStatus.FAILED, charge_conflict=True
        )

        self.assertEqual(self.list_refs(charge_status="failed"), {"0599000003", "0599000008"})
        self.assertEqual(
            self.list_refs(charge_status="needs_verification"),
            {"0599000005", "0599000006", "0599000008"},
        )
        self.assertEqual(self.list_refs(entry_mode="manual"), {"0599000001"})
        self.assertEqual(len(self.list_refs(entry_mode="auto_charge")), 7)

    def test_list_shows_conflict_and_details(self):
        self.manual_sale(
            charge_status=Sale.ChargeStatus.FAILED,
            charge_conflict=True,
            charge_provider_ref="REF-777",
            charge_plan_name="Plan 30",
            charge_provider_cost=Decimal("12.00"),
            charge_message="provider note",
        )
        resp = self.client.get(reverse("sales:management_sale_list"))
        self.assertContains(resp, "Auto charge")
        self.assertContains(resp, "Charge conflict")
        self.assertContains(resp, "Cost difference")
        self.assertContains(resp, "REF-777")
        self.assertContains(resp, "Plan 30")
        self.assertNotContains(resp, "provider note")

    def test_unsent_charge_says_so_and_keeps_the_settle_actions(self):
        from providers.models import ProviderOperation

        sale = self.manual_sale(
            charge_status=Sale.ChargeStatus.UNVERIFIED,
            charge_message="الشحن قيد المتابعة",
        )
        ProviderOperation.objects.create(
            provider_key="sky",
            service="charge",
            phone=sale.reference_number,
            outcome="pending",
            message="الشحن قيد المتابعة",
            source="employee",
            sale_id=sale.pk,
            detail="NotImplementedError",
        )
        resp = self.client.get(reverse("sales:management_sale_list"))
        self.assertContains(resp, "Not sent")
        self.assertNotContains(resp, "Needs verification")
        self.assertContains(resp, "This request never reached the provider.")
        self.assertContains(resp, reverse("sales:sale_charge_recheck", args=[sale.pk]))
        self.assertContains(resp, 'name="result" value="success"')
        self.assertContains(resp, 'name="result" value="failed"')
        self.assertContains(resp, 'data-rd-charge-open="sale-actions-row-charge-%s"' % sale.pk)
        self.assertContains(resp, 'id="sale-actions-row-charge-%s"' % sale.pk)
        self.assertNotContains(resp, "popovertarget=")

    def test_open_charge_shows_actions_with_confirmation(self):
        sale = self.manual_sale(charge_status=Sale.ChargeStatus.UNVERIFIED)
        resp = self.client.get(reverse("sales:management_sale_list"))
        self.assertContains(resp, reverse("sales:sale_charge_recheck", args=[sale.pk]))
        self.assertContains(resp, reverse("sales:sale_charge_resolve", args=[sale.pk]))
        self.assertContains(resp, 'name="result" value="success"')
        self.assertContains(resp, "Mark this charge as successful?")

    def test_resolve_failed_requires_management_and_reverses(self):
        sale = self.manual_sale(charge_status=Sale.ChargeStatus.UNVERIFIED)
        url = reverse("sales:sale_charge_resolve", args=[sale.pk])

        self.client.force_login(self.user)
        resp = self.client.post(url, {"result": "failed"})
        self.assertRedirects(resp, reverse("core:forbidden"), fetch_redirect_response=False)
        sale.refresh_from_db()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.UNVERIFIED)
        self.assertNotEqual(sale.status, Sale.Status.CANCELLED)

        self.client.force_login(self.manager)
        resp = self.client.post(url, {"result": "failed"})
        self.assertEqual(resp.status_code, 302)
        sale.refresh_from_db()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.FAILED)
        self.assertEqual(sale.status, Sale.Status.CANCELLED)
        self.assertEqual(self.balance(), START)

    def test_resolve_is_post_only(self):
        sale = self.manual_sale(charge_status=Sale.ChargeStatus.UNVERIFIED)
        resp = self.client.get(reverse("sales:sale_charge_resolve", args=[sale.pk]))
        self.assertEqual(resp.status_code, 405)

    def test_resolve_redirect_keeps_list_filters(self):
        sale = self.manual_sale(charge_status=Sale.ChargeStatus.UNVERIFIED)
        back = reverse("sales:management_sale_list") + "?charge_status=unverified&page=1"
        resp = self.client.post(
            reverse("sales:sale_charge_resolve", args=[sale.pk]),
            {"result": "success"},
            HTTP_REFERER="http://testserver" + back,
        )
        self.assertRedirects(resp, "http://testserver" + back, fetch_redirect_response=False)
        sale.refresh_from_db()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.SUCCESS)

    def test_resolve_success_on_unsent_charge_shows_error(self):
        sale = self.charge_without_worker()
        resp = self.client.post(
            reverse("sales:sale_charge_resolve", args=[sale.pk]), {"result": "success"}, follow=True
        )
        self.assertContains(resp, "This charge was never sent to the provider.")
        sale.refresh_from_db()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.PROCESSING)

    def test_resolve_htmx_returns_row_partial(self):
        sale = self.manual_sale(charge_status=Sale.ChargeStatus.UNVERIFIED)
        resp = self.client.post(
            reverse("sales:sale_charge_resolve", args=[sale.pk]) + "?layout=row",
            {"result": "success"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f'id="sale-row-{sale.pk}"')
        self.assert_charge_chip(resp, "success", "Charged")
        self.assertNotContains(resp, "rd-charge-tag--unverified")
        self.assertIn("rdToast", resp["HX-Trigger"])
        sale.refresh_from_db()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.SUCCESS)

    def test_resolve_htmx_error_keeps_row(self):
        sale = self.charge_without_worker()
        resp = self.client.post(
            reverse("sales:sale_charge_resolve", args=[sale.pk]) + "?layout=row",
            {"result": "success"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["HX-Reswap"], "none")
        self.assertIn("rdSaleActionError", resp["HX-Trigger"])
        self.assertIn("never sent to the provider", resp["HX-Trigger"])
        self.assertEqual(resp.content, b"")
        sale.refresh_from_db()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.PROCESSING)

    def test_recheck_htmx_error_on_closed_charge(self):
        sale = self.manual_sale(charge_status=Sale.ChargeStatus.SUCCESS)
        with patch("sales.tasks.check_sale_charge.delay") as delay:
            resp = self.client.post(
                reverse("sales:sale_charge_recheck", args=[sale.pk]) + "?layout=card",
                HTTP_HX_REQUEST="true",
            )
        delay.assert_not_called()
        self.assertEqual(resp["HX-Reswap"], "none")
        self.assertIn("Only charges in progress or unverified", resp["HX-Trigger"])

    def test_recheck_htmx_shows_checking_state_and_polls(self):
        sale = self.manual_sale(charge_status=Sale.ChargeStatus.UNVERIFIED)
        with patch("sales.tasks.check_sale_charge.delay") as delay:
            resp = self.client.post(
                reverse("sales:sale_charge_recheck", args=[sale.pk]) + "?layout=row",
                HTTP_HX_REQUEST="true",
            )
        delay.assert_called_once_with(sale.pk)
        self.assertEqual(resp.status_code, 200)
        toast = json.loads(resp["HX-Trigger"])["rdToast"]
        self.assertEqual(toast["level"], "success")
        self.assertIn("Checking this charge with the provider", toast["message"])
        self.assertContains(resp, f'id="sale-row-{sale.pk}"')
        self.assertContains(resp, "Checking with the provider")
        self.assert_polls(resp)
        self.assertContains(resp, f"layout=row&amp;polls={RECHECK_POLLS}&amp;since=")
        self.assertNotContains(resp, reverse("sales:sale_charge_recheck", args=[sale.pk]))

    def test_recheck_poll_counts_down_and_stops(self):
        sale = self.manual_sale(charge_status=Sale.ChargeStatus.UNVERIFIED)
        row_url = reverse("sales:sale_charge_row", args=[sale.pk])

        resp = self.client.get(row_url, {"layout": "card", "polls": 3, "since": ""})
        self.assertContains(resp, "polls=2&amp;since=")
        self.assertContains(resp, "Checking with the provider")

        resp = self.client.get(row_url, {"layout": "card", "polls": 1, "since": ""})
        self.assertNotContains(resp, "hx-trigger")
        self.assertNotContains(resp, "Checking with the provider")

        # The check landed: charge_checked_at moved, so the wait ends early.
        Sale.objects.filter(pk=sale.pk).update(charge_checked_at=timezone.now())
        resp = self.client.get(row_url, {"layout": "card", "polls": 5, "since": ""})
        self.assertNotContains(resp, "hx-trigger")
        self.assertContains(resp, reverse("sales:sale_charge_recheck", args=[sale.pk]))

    def test_provider_message_is_escaped_in_details(self):
        self.manual_sale(
            charge_status=Sale.ChargeStatus.UNVERIFIED,
            charge_message='<script>alert("x")</script>',
        )
        resp = self.client.get(reverse("sales:management_sale_list"))
        self.assertNotContains(resp, '<script>alert("x")</script>')
        self.assertContains(resp, "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;")

    def test_recheck_queues_check(self):
        sale = self.unverified()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.UNVERIFIED)
        resp = self.client.post(reverse("sales:sale_charge_recheck", args=[sale.pk]))
        self.assertEqual(resp.status_code, 302)
        sale.refresh_from_db()
        self.assertEqual(sale.charge_status, Sale.ChargeStatus.SUCCESS)

    def test_recheck_requires_management(self):
        sale = self.manual_sale(charge_status=Sale.ChargeStatus.UNVERIFIED)
        self.client.force_login(self.user)
        resp = self.client.post(reverse("sales:sale_charge_recheck", args=[sale.pk]))
        self.assertRedirects(resp, reverse("core:forbidden"), fetch_redirect_response=False)

    def test_management_row_layout(self):
        sale = self.manual_sale(charge_status=Sale.ChargeStatus.PROCESSING)
        resp = self.client.get(reverse("sales:sale_charge_row", args=[sale.pk]) + "?layout=row")
        self.assertContains(resp, f'id="sale-row-{sale.pk}"')
        self.assert_polls(resp)
        resp = self.client.get(reverse("sales:sale_charge_row", args=[sale.pk]) + "?layout=card")
        self.assertContains(resp, f'id="sale-card-{sale.pk}"')
        self.assertContains(resp, "?layout=card")

    def test_pending_and_on_account_lists_show_the_charge_chip(self):
        pending = self.manual_sale(
            reference="0599111001",
            charge_status=Sale.ChargeStatus.SUCCESS,
        )
        customer = Customer.objects.create(name="On Account", created_by=self.manager)
        on_account = create_sale(
            user=self.user,
            customer=customer,
            on_account=True,
            **self.sale_kwargs(reference_number="0599111002", payment_method=None),
        )
        Sale.objects.filter(pk=on_account.pk).update(
            entry_mode=Sale.EntryMode.AUTO_CHARGE,
            charge_status=Sale.ChargeStatus.FAILED,
        )

        pending_page = self.client.get(reverse("sales:pending_payments"))
        self.assertContains(pending_page, pending.reference_number)
        self.assert_charge_chip(pending_page, "success", "Charged")
        self.assertContains(pending_page, "Auto charge")

        awaiting_page = self.client.get(reverse("sales:awaiting_approvals"))
        self.assertContains(awaiting_page, on_account.reference_number)
        self.assert_charge_chip(awaiting_page, "failed", "Charge failed")
        self.assertContains(awaiting_page, "Auto charge")

    def test_employee_ignores_recheck_poll_params(self):
        sale = self.manual_sale(charge_status=Sale.ChargeStatus.UNVERIFIED)
        self.client.force_login(self.user)
        resp = self.client.get(
            reverse("sales:sale_charge_row", args=[sale.pk]),
            {"layout": "row", "polls": 5, "since": ""},
        )
        self.assertNotContains(resp, "hx-trigger")
        self.assertNotContains(resp, "Checking with the provider")
