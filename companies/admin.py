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
    list_display = ("name", "company", "sort_order", "is_active", "default_package")
    list_filter = ("company", "is_active")
    search_fields = ("name", "company__name")
    autocomplete_fields = ("default_package",)
    inlines = [ProductVariantInline]

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "default_package":
            obj = kwargs.get("obj")
            if obj:
                kwargs["queryset"] = Product.objects.filter(line=obj).order_by("variant_label")
            else:
                kwargs["queryset"] = Product.objects.none()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("display_name", "line", "cost_price", "default_sell_price", "is_active")
    list_filter = ("line__company", "is_active")
    search_fields = ("variant_label", "line__name")
    autocomplete_fields = ("line",)

    @admin.display(description="Name")
    def display_name(self, obj):
        return obj.display_name
