from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import UserProfile
from expenses.models import Expense

User = get_user_model()


class ExpenseDeleteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("mgmt_del", password="secret")
        p = cls.user.profile
        p.role = UserProfile.Role.MANAGEMENT
        p.save(update_fields=["role"])

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.user)
        self.expense = Expense.objects.create(
            title="To remove",
            category="test",
            amount=Decimal("1.00"),
            date=date(2026, 1, 10),
            created_by=self.user,
        )

    def test_delete_removes_expense(self):
        url = reverse("expenses:expense_delete", args=[self.expense.pk])
        r = self.client.get(reverse("expenses:expense_list"))
        token = r.context["csrf_token"]
        r = self.client.post(
            url,
            {"next": reverse("expenses:expense_list"), "csrfmiddlewaretoken": str(token)},
        )
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Expense.objects.filter(pk=self.expense.pk).exists())

    def test_delete_requires_post(self):
        url = reverse("expenses:expense_delete", args=[self.expense.pk])
        r = self.client.get(url)
        self.assertEqual(r.status_code, 405)
        self.assertTrue(Expense.objects.filter(pk=self.expense.pk).exists())
