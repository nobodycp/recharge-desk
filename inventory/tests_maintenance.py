from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import UserProfile
from companies.models import Company, ProductLine
from customers.models import Customer
from inventory.models import SimCard, SimStockBalance, SimStockMovement
from inventory.services import (
    allocate_to_customer,
    clear_balance,
    delete_balance_row,
    delete_movement,
    mark_damaged,
    receive_main_stock,
    record_manual_sale,
    return_from_customer,
    set_balance_quantity,
)

User = get_user_model()


class BalanceMaintenanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name="Co", current_balance=Decimal("0"))
        cls.line = ProductLine.objects.create(company=cls.company, name="Sleekom")
        cls.user = User.objects.create_user("mgr_maint", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )

    def test_set_clear_and_delete_balance(self):
        receive_main_stock(product_line=self.line, qty=5, notes="", user=self.user)
        balance = SimStockBalance.objects.get(
            location=SimStockBalance.Location.MAIN, product_line=self.line
        )
        set_balance_quantity(
            balance=balance, new_quantity=9, reason="test set", user=self.user
        )
        balance.refresh_from_db()
        self.assertEqual(balance.quantity, 9)
        clear_balance(balance=balance, reason="test clear", user=self.user)
        balance.refresh_from_db()
        self.assertEqual(balance.quantity, 0)
        delete_balance_row(balance=balance, user=self.user)
        self.assertFalse(SimStockBalance.objects.filter(pk=balance.pk).exists())

    def test_delete_movement_blocks_sale_linked(self):
        receive_main_stock(product_line=self.line, qty=1, notes="", user=self.user)
        movement = SimStockMovement.objects.filter(
            movement_type=SimStockMovement.MovementType.MAIN_RECEIVE
        ).first()
        delete_movement(movement=movement, user=self.user)
        self.assertFalse(SimStockMovement.objects.filter(pk=movement.pk).exists())

    def test_set_balance_quantity_allows_blank_reason_when_not_required(self):
        receive_main_stock(product_line=self.line, qty=5, notes="", user=self.user)
        balance = SimStockBalance.objects.get(
            location=SimStockBalance.Location.MAIN, product_line=self.line
        )
        set_balance_quantity(
            balance=balance,
            new_quantity=3,
            reason="",
            user=self.user,
            require_reason=False,
        )
        balance.refresh_from_db()
        self.assertEqual(balance.quantity, 3)

    def test_set_balance_quantity_still_requires_reason_by_default(self):
        receive_main_stock(product_line=self.line, qty=5, notes="", user=self.user)
        balance = SimStockBalance.objects.get(
            location=SimStockBalance.Location.MAIN, product_line=self.line
        )
        with self.assertRaises(ValueError):
            set_balance_quantity(
                balance=balance, new_quantity=3, reason="", user=self.user
            )


class ManualSaleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name="Co", current_balance=Decimal("0"))
        cls.line = ProductLine.objects.create(company=cls.company, name="Sleekom")
        cls.user = User.objects.create_user("mgr_manual_sale", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        cls.customer = Customer.objects.create(name="Manual Sale Customer", created_by=cls.user)

    def test_record_manual_sale_deducts_customer_stock(self):
        receive_main_stock(product_line=self.line, qty=10, notes="", user=self.user)
        allocate_to_customer(
            customer=self.customer, product_line=self.line, qty=5, notes="", user=self.user
        )
        balance = SimStockBalance.objects.get(
            location=SimStockBalance.Location.CUSTOMER,
            product_line=self.line,
            customer=self.customer,
        )
        record_manual_sale(balance=balance, qty=2, notes="Sold off-app", user=self.user)
        balance.refresh_from_db()
        self.assertEqual(balance.quantity, 3)
        movement = SimStockMovement.objects.filter(
            movement_type=SimStockMovement.MovementType.MANUAL_SALE
        ).first()
        self.assertIsNotNone(movement)
        self.assertEqual(movement.quantity, 2)
        self.assertEqual(movement.notes, "Sold off-app")

    def test_record_manual_sale_allows_blank_notes(self):
        receive_main_stock(product_line=self.line, qty=4, notes="", user=self.user)
        allocate_to_customer(
            customer=self.customer, product_line=self.line, qty=4, notes="", user=self.user
        )
        balance = SimStockBalance.objects.get(
            location=SimStockBalance.Location.CUSTOMER,
            product_line=self.line,
            customer=self.customer,
        )
        record_manual_sale(balance=balance, qty=1, notes="", user=self.user)
        balance.refresh_from_db()
        self.assertEqual(balance.quantity, 3)

    def test_record_manual_sale_rejects_insufficient_stock(self):
        receive_main_stock(product_line=self.line, qty=1, notes="", user=self.user)
        allocate_to_customer(
            customer=self.customer, product_line=self.line, qty=1, notes="", user=self.user
        )
        balance = SimStockBalance.objects.get(
            location=SimStockBalance.Location.CUSTOMER,
            product_line=self.line,
            customer=self.customer,
        )
        with self.assertRaises(ValueError):
            record_manual_sale(balance=balance, qty=5, notes="", user=self.user)


class DeleteMovementReversesStockTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name="Co", current_balance=Decimal("0"))
        cls.line = ProductLine.objects.create(company=cls.company, name="Sleekom")
        cls.user = User.objects.create_user("mgr_undo", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        cls.customer = Customer.objects.create(name="Undo Customer", created_by=cls.user)

    def _main_balance(self):
        return SimStockBalance.objects.get(
            location=SimStockBalance.Location.MAIN, product_line=self.line
        )

    def _customer_balance(self):
        return SimStockBalance.objects.get(
            location=SimStockBalance.Location.CUSTOMER,
            product_line=self.line,
            customer=self.customer,
        )

    def test_delete_main_receive_movement_reverses_balance(self):
        receive_main_stock(product_line=self.line, qty=5, notes="", user=self.user)
        movement = SimStockMovement.objects.get(
            movement_type=SimStockMovement.MovementType.MAIN_RECEIVE
        )
        delete_movement(movement=movement, user=self.user)
        self.assertEqual(self._main_balance().quantity, 0)

    def test_delete_main_receive_with_serial_deletes_card(self):
        receive_main_stock(
            product_line=self.line, qty=1, notes="", user=self.user, serials=["1111"]
        )
        movement = SimStockMovement.objects.get(
            movement_type=SimStockMovement.MovementType.MAIN_RECEIVE
        )
        self.assertTrue(SimCard.objects.filter(serial_or_iccid="1111").exists())
        delete_movement(movement=movement, user=self.user)
        self.assertFalse(SimCard.objects.filter(serial_or_iccid="1111").exists())
        self.assertEqual(self._main_balance().quantity, 0)

    def test_delete_allocate_movement_reverses_both_balances(self):
        receive_main_stock(product_line=self.line, qty=10, notes="", user=self.user)
        allocate_to_customer(
            customer=self.customer, product_line=self.line, qty=4, notes="", user=self.user
        )
        movement = SimStockMovement.objects.get(
            movement_type=SimStockMovement.MovementType.ALLOCATE_TO_CUSTOMER
        )
        delete_movement(movement=movement, user=self.user)
        self.assertEqual(self._main_balance().quantity, 10)
        self.assertEqual(self._customer_balance().quantity, 0)

    def test_delete_allocate_with_serial_moves_card_back_to_main(self):
        receive_main_stock(
            product_line=self.line, qty=1, notes="", user=self.user, serials=["2222"]
        )
        allocate_to_customer(
            customer=self.customer,
            product_line=self.line,
            qty=1,
            notes="",
            user=self.user,
            serials=["2222"],
        )
        movement = SimStockMovement.objects.get(
            movement_type=SimStockMovement.MovementType.ALLOCATE_TO_CUSTOMER
        )
        delete_movement(movement=movement, user=self.user)
        card = SimCard.objects.get(serial_or_iccid="2222")
        self.assertEqual(card.status, SimCard.Status.IN_MAIN)
        self.assertIsNone(card.customer)
        self.assertEqual(self._main_balance().quantity, 1)
        self.assertEqual(self._customer_balance().quantity, 0)

    def test_delete_return_from_customer_reverses_both_balances(self):
        receive_main_stock(product_line=self.line, qty=10, notes="", user=self.user)
        allocate_to_customer(
            customer=self.customer, product_line=self.line, qty=6, notes="", user=self.user
        )
        return_from_customer(
            customer=self.customer, product_line=self.line, qty=2, notes="", user=self.user
        )
        movement = SimStockMovement.objects.get(
            movement_type=SimStockMovement.MovementType.RETURN_FROM_CUSTOMER
        )
        delete_movement(movement=movement, user=self.user)
        self.assertEqual(self._main_balance().quantity, 4)
        self.assertEqual(self._customer_balance().quantity, 6)

    def test_delete_damaged_movement_restores_quantity(self):
        receive_main_stock(product_line=self.line, qty=10, notes="", user=self.user)
        allocate_to_customer(
            customer=self.customer, product_line=self.line, qty=5, notes="", user=self.user
        )
        mark_damaged(balance=self._customer_balance(), qty=3, notes="", user=self.user)
        self.assertEqual(self._customer_balance().quantity, 2)
        movement = SimStockMovement.objects.get(
            movement_type=SimStockMovement.MovementType.DAMAGED
        )
        delete_movement(movement=movement, user=self.user)
        self.assertEqual(self._customer_balance().quantity, 5)

    def test_delete_manual_sale_movement_restores_quantity(self):
        receive_main_stock(product_line=self.line, qty=10, notes="", user=self.user)
        allocate_to_customer(
            customer=self.customer, product_line=self.line, qty=5, notes="", user=self.user
        )
        record_manual_sale(balance=self._customer_balance(), qty=2, notes="", user=self.user)
        self.assertEqual(self._customer_balance().quantity, 3)
        movement = SimStockMovement.objects.get(
            movement_type=SimStockMovement.MovementType.MANUAL_SALE
        )
        delete_movement(movement=movement, user=self.user)
        self.assertEqual(self._customer_balance().quantity, 5)

    def test_delete_set_quantity_adjustment_reverses_change(self):
        receive_main_stock(product_line=self.line, qty=5, notes="", user=self.user)
        set_balance_quantity(
            balance=self._main_balance(), new_quantity=9, reason="test", user=self.user
        )
        self.assertEqual(self._main_balance().quantity, 9)
        movement = SimStockMovement.objects.filter(
            movement_type=SimStockMovement.MovementType.ADJUSTMENT
        ).latest("id")
        delete_movement(movement=movement, user=self.user)
        self.assertEqual(self._main_balance().quantity, 5)

    def test_delete_movement_rejects_when_reversal_would_go_negative(self):
        receive_main_stock(product_line=self.line, qty=5, notes="", user=self.user)
        allocate_to_customer(
            customer=self.customer, product_line=self.line, qty=5, notes="", user=self.user
        )
        receive_movement = SimStockMovement.objects.get(
            movement_type=SimStockMovement.MovementType.MAIN_RECEIVE
        )
        # Main stock is now 0 (all 5 were allocated to the customer), so
        # reversing the original receive would push it negative.
        with self.assertRaises(ValueError):
            delete_movement(movement=receive_movement, user=self.user)
        self.assertEqual(self._main_balance().quantity, 0)


class CustomerBalanceActionViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name="Co", current_balance=Decimal("0"))
        cls.line = ProductLine.objects.create(company=cls.company, name="Sleekom")
        cls.user = User.objects.create_user("mgr_view", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        cls.customer = Customer.objects.create(name="Filter Customer", created_by=cls.user)

    def setUp(self):
        self.client.force_login(self.user)
        receive_main_stock(product_line=self.line, qty=10, notes="", user=self.user)
        allocate_to_customer(
            customer=self.customer, product_line=self.line, qty=5, notes="", user=self.user
        )
        self.balance = SimStockBalance.objects.get(
            location=SimStockBalance.Location.CUSTOMER,
            product_line=self.line,
            customer=self.customer,
        )

    def test_customer_set_edits_quantity_without_reason(self):
        url = reverse("inventory:customer_set", args=[self.customer.pk, self.balance.pk])
        resp = self.client.post(url, {"quantity": 8})
        self.assertEqual(resp.status_code, 302)
        self.balance.refresh_from_db()
        self.assertEqual(self.balance.quantity, 8)

    def test_customer_manual_sale_deducts_with_optional_note(self):
        url = reverse("inventory:customer_manual_sale", args=[self.customer.pk, self.balance.pk])
        resp = self.client.post(url, {"quantity": 2, "notes": "Sold to a walk-in"})
        self.assertEqual(resp.status_code, 302)
        self.balance.refresh_from_db()
        self.assertEqual(self.balance.quantity, 3)
        movement = SimStockMovement.objects.filter(
            movement_type=SimStockMovement.MovementType.MANUAL_SALE
        ).first()
        self.assertIsNotNone(movement)
        self.assertEqual(movement.notes, "Sold to a walk-in")

    def test_customer_list_action_redirect_preserves_filter_query_string(self):
        list_url = reverse("inventory:customers")
        next_url = f"{list_url}?q=Filter"
        clear_url = reverse("inventory:customer_clear", args=[self.customer.pk, self.balance.pk])
        resp = self.client.post(clear_url, {"next": next_url})
        self.assertRedirects(resp, next_url, fetch_redirect_response=False)

    def test_customer_list_view_renders_next_with_query_string(self):
        list_url = reverse("inventory:customers")
        resp = self.client.get(list_url, {"q": "Filter"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f"{list_url}?q=Filter")

    def test_customer_list_groups_lines_under_one_customer_card(self):
        line2 = ProductLine.objects.create(company=self.company, name="ALOHA")
        receive_main_stock(product_line=line2, qty=10, notes="", user=self.user)
        allocate_to_customer(
            customer=self.customer, product_line=line2, qty=4, notes="", user=self.user
        )
        other = Customer.objects.create(name="Other Dealer", created_by=self.user)
        allocate_to_customer(
            customer=other, product_line=self.line, qty=2, notes="", user=self.user
        )

        resp = self.client.get(reverse("inventory:customers"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "rd-customer-stock-card")
        self.assertContains(resp, "rd-customer-stock-filter")
        self.assertContains(resp, "rd-allocate-panel")
        self.assertNotContains(resp, 'rd-allocate-panel" open')
        self.assertEqual(resp.content.decode().count("Filter Customer"), 1)
        self.assertContains(resp, "Sleekom")
        self.assertContains(resp, "ALOHA")
        groups = resp.context["customer_groups"]
        self.assertEqual(len(groups), 2)
        filter_group = next(g for g in groups if g["customer"].pk == self.customer.pk)
        self.assertEqual(len(filter_group["balances"]), 2)
        self.assertEqual(filter_group["total"], 9)

    def test_customer_list_live_filter_returns_results_partial(self):
        url = reverse("inventory:customers")
        resp = self.client.get(
            url,
            {"q": "Filter"},
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="rd-customer-stock-results",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "inventory/partials/customer_stock_results.html")
        self.assertContains(resp, "Filter Customer")
        self.assertContains(resp, "rd-customer-stock-results")

        resp = self.client.get(
            url,
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="body",
        )
        self.assertTemplateUsed(resp, "inventory/customer_list.html")

    def test_movements_customer_filter_is_text_search(self):
        url = reverse("inventory:movements")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'name="customer"')
        self.assertContains(resp, 'type="text"')
        self.assertNotContains(resp, '<select name="customer"')

        resp = self.client.get(url, {"customer": "Filter"})
        self.assertEqual(resp.status_code, 200)
        page = list(resp.context["page_obj"])
        self.assertTrue(page)
        self.assertTrue(all(m.customer_id == self.customer.pk for m in page))
        self.assertEqual(resp.context["filter_active"], 1)

        resp = self.client.get(url, {"customer": "zzz-no-match"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(list(resp.context["page_obj"]), [])


class InventoryMobileLayoutTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name="Co", current_balance=Decimal("0"))
        cls.line = ProductLine.objects.create(company=cls.company, name="Sleekom")
        cls.user = User.objects.create_user("mgr_mobile_inv", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        receive_main_stock(product_line=cls.line, qty=5, notes="", user=cls.user)

    def setUp(self):
        self.client.force_login(self.user)

    def test_overview_and_main_include_mobile_card_stack(self):
        for name in ("inventory:overview", "inventory:main"):
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 200, name)
            self.assertContains(resp, "rd-mobile-stack")
            self.assertContains(resp, "rd-mobile-row-card")
            self.assertContains(resp, "d-md-none")
            self.assertContains(resp, "Sleekom")
