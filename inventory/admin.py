from django.contrib import admin

from inventory.models import SimCard, SimStockBalance, SimStockMovement


@admin.register(SimStockBalance)
class SimStockBalanceAdmin(admin.ModelAdmin):
    list_display = ("location", "customer", "product_line", "quantity")
    list_filter = ("location", "product_line")


@admin.register(SimStockMovement)
class SimStockMovementAdmin(admin.ModelAdmin):
    list_display = ("movement_type", "product_line", "quantity", "customer", "sale", "created_at")
    list_filter = ("movement_type",)
    readonly_fields = ("created_at",)


@admin.register(SimCard)
class SimCardAdmin(admin.ModelAdmin):
    list_display = ("serial_or_iccid", "product_line", "status", "customer", "sale", "created_at")
    list_filter = ("status", "product_line")
    search_fields = ("serial_or_iccid",)
