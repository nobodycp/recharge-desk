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
        # ``path`` (not ``str``/``slug``): references can include ``/`` (dates,
        # shipment ids) and Arabic digits/text. ``str`` is [^/]+ and 500s the
        # whole statement when any matching row has a slash in the key.
        "management/reports/phone-lines/edit/<path:ref_key>/",
        views_stale_phones.stale_phone_edit,
        name="stale_phone_edit",
    ),
    path(
        "management/reports/phone-lines/sim/<path:ref_key>/",
        views_stale_phones.stale_phone_sim_save,
        name="stale_phone_sim_save",
    ),
    path(
        "management/reports/phone-lines/renew/<path:ref_key>/",
        views_stale_phones.phone_line_renew,
        name="phone_line_renew",
    ),
    path(
        "management/reports/phone-lines/delete/<path:ref_key>/",
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
