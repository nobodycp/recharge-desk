from django.contrib import admin

from expenses.models import Expense


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ("date", "title", "category", "amount", "created_by")
    list_filter = ("category",)
