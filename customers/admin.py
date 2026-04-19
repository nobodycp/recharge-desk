from django.contrib import admin

from customers.models import Customer, CustomerLedger, CustomerPayment, CustomerPhone


class CustomerPhoneInline(admin.TabularInline):
    model = CustomerPhone
    extra = 0


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "current_balance", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "phones__phone")
    readonly_fields = ("current_balance", "created_at", "updated_at")
    inlines = [CustomerPhoneInline]


@admin.register(CustomerPayment)
class CustomerPaymentAdmin(admin.ModelAdmin):
    list_display = ("customer", "amount", "payment_method", "created_by", "created_at")
    list_filter = ("payment_method",)
    search_fields = ("customer__name",)
    readonly_fields = ("created_at",)


@admin.register(CustomerLedger)
class CustomerLedgerAdmin(admin.ModelAdmin):
    list_display = ("customer", "entry_type", "amount", "sale", "payment", "created_at")
    list_filter = ("entry_type",)
    search_fields = ("customer__name",)
    readonly_fields = ("created_at",)


@admin.register(CustomerPhone)
class CustomerPhoneAdmin(admin.ModelAdmin):
    list_display = ("customer", "phone", "label")
    search_fields = ("customer__name", "phone")
