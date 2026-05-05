from django.urls import path

from reports import views
from reports import views_stale_phones

app_name = "reports"

urlpatterns = [
    path("management/", views.dashboard, name="dashboard"),
    path("management/reports/profit/", views.profit_report, name="profit_report"),
    path("management/reports/sales/", views.sales_report, name="sales_report"),
    path("management/reports/employees/", views.employee_report, name="employee_report"),
    path("management/reports/company/<int:pk>/", views.company_report, name="company_report"),
    path(
        # ``str`` (not ``slug``): reference can be Arabic digits/text or shipment ids.
        "management/reports/stale-phones/edit/<str:ref_key>/",
        views_stale_phones.stale_phone_edit,
        name="stale_phone_edit",
    ),
    path(
        "management/reports/stale-phones/dismiss/<str:ref_key>/",
        views_stale_phones.stale_phone_dismiss,
        name="stale_phone_dismiss",
    ),
    path(
        "management/reports/stale-phones/",
        views_stale_phones.stale_phones_report,
        name="stale_phones_report",
    ),
]
