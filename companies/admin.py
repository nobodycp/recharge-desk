from django.contrib import admin

from companies.models import Company, Product, ProductLine


class ProductVariantInline(admin.TabularInline):
    model = Product
    extra = 0
    fields = ("variant_label", "icon", "cost_price", "default_sell_price", "is_active")


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "opening_balance", "current_balance", "is_active")
    search_fields = ("name",)
    fields = ("name", "icon", "opening_balance", "current_balance", "notes", "is_active")
    readonly_fields = ("current_balance",)


@admin.register(ProductLine)
class ProductLineAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "sort_order", "is_active")
    list_filter = ("company", "is_active")
    search_fields = ("name", "company__name")
    inlines = [ProductVariantInline]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("display_name", "line", "cost_price", "default_sell_price", "is_active")
    list_filter = ("line__company", "is_active")
    search_fields = ("variant_label", "line__name")
    autocomplete_fields = ("line",)

    @admin.display(description="Name")
    def display_name(self, obj):
        return obj.display_name
