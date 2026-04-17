from django.contrib import admin

from sales.models import CompanyBalanceTransaction, PaymentMethod, Sale


@admin.register(PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active")
    fields = ("name", "icon", "is_active")


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "company",
        "product",
        "reference_number",
        "sell_price_actual",
        "is_esim",
        "status",
        "created_by",
    )
    list_filter = ("status", "company", "payment_method", "is_esim")
    search_fields = ("reference_number", "payer_name")


@admin.register(CompanyBalanceTransaction)
class CompanyBalanceTransactionAdmin(admin.ModelAdmin):
    list_display = ("created_at", "company", "entry_type", "amount", "reference_type", "reference_id")
    list_filter = ("entry_type", "reference_type")
