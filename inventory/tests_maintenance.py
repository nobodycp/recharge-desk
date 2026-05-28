from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import UserProfile
from companies.models import Company, ProductLine
from inventory.models import SimStockBalance, SimStockMovement
from inventory.services import (
    clear_balance,
    delete_balance_row,
    delete_movement,
    receive_main_stock,
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
