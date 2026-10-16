from django.urls import path

from reports import views
from reports import views_provider_ops
from reports import views_stale_phones

app_name = "reports"

urlpatterns = [
    path("management/", views.dashboard, name="dashboard"),
    path("management/reports/profit/", views.profit_report, name="profit_report"),
    path("management/reports/sales/", views.sales_report, name="sales_report"),
    path("management/reports/employees/", views.employee_report, name="employee_report"),
    path("management/reports/company/<int:pk>/", views.company_report, name="company_report"),
    path(
        "management/reports/providers/",
        views_provider_ops.provider_operations,
        name="provider_operations",
    ),
    path(
        # ``str`` (not ``slug``): reference can be Arabic digits/text or shipment ids.
        "management/reports/phone-lines/edit/<str:ref_key>/",
        views_stale_phones.stale_phone_edit,
        name="stale_phone_edit",
    ),
    path(
        "management/reports/phone-lines/sim/<str:ref_key>/",
        views_stale_phones.stale_phone_sim_save,
        name="stale_phone_sim_save",
    ),
    path(
        "management/reports/phone-lines/renew/<str:ref_key>/",
        views_stale_phones.phone_line_renew,
        name="phone_line_renew",
    ),
    path(
        "management/reports/phone-lines/delete/<str:ref_key>/",
        views_stale_phones.phone_line_delete,
        name="phone_line_delete",
    ),
    path(
        "management/reports/phone-lines/bulk/",
        views_stale_phones.phone_lines_bulk,
        name="phone_lines_bulk",
    ),
    path(
        "management/reports/phone-lines/",
        views_stale_phones.phone_lines_report,
        name="phone_lines_report",
    ),
]
