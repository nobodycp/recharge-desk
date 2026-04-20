from django.urls import path

from reports import views

app_name = "reports"

urlpatterns = [
    path("management/", views.dashboard, name="dashboard"),
    path("management/reports/profit/", views.profit_report, name="profit_report"),
    path("management/reports/sales/", views.sales_report, name="sales_report"),
    path("management/reports/employees/", views.employee_report, name="employee_report"),
    path("management/reports/company/<int:pk>/", views.company_report, name="company_report"),
]
