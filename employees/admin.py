from django.contrib import admin

from employees.models import EmployeeLedgerEntry, EmployeeProfile


class EmployeeLedgerInline(admin.TabularInline):
    model = EmployeeLedgerEntry
    extra = 0
    readonly_fields = ("created_at",)
    fields = (
        "entry_type",
        "amount",
        "salary_month",
        "payer_name",
        "phone",
        "reference_sale",
        "notes",
        "created_by",
        "created_at",
    )


@admin.register(EmployeeProfile)
class EmployeeProfileAdmin(admin.ModelAdmin):
    list_display = ("display_name", "monthly_salary", "current_balance", "is_active")
    list_filter = ("is_active",)
    search_fields = ("user__username", "user__profile__full_name")
    inlines = [EmployeeLedgerInline]


@admin.register(EmployeeLedgerEntry)
class EmployeeLedgerEntryAdmin(admin.ModelAdmin):
    list_display = ("employee", "entry_type", "amount", "salary_month", "created_at")
    list_filter = ("entry_type",)
    search_fields = ("payer_name", "phone", "employee__user__username")
    raw_id_fields = ("employee", "reference_sale", "created_by")
