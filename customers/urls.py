from django.urls import path

from customers import views

app_name = "customers"

urlpatterns = [
    path("management/customers/", views.customer_list, name="customer_list"),
    path(
        "management/customers/export.csv",
        views.customers_export_csv,
        name="customers_export_csv",
    ),
    path(
        "management/customers/payments/export.csv",
        views.customer_payments_export_csv,
        name="customer_payments_export_csv",
    ),
    path("management/customers/new/", views.customer_create, name="customer_create"),
    path("management/customers/<int:pk>/", views.customer_detail, name="customer_detail"),
    path("management/customers/<int:pk>/edit/", views.customer_edit, name="customer_edit"),
    path(
        "management/customers/<int:pk>/statement/",
        views.customer_statement,
        name="customer_statement",
    ),
    path(
        "management/customers/<int:pk>/statement.csv",
        views.customer_statement_csv,
        name="customer_statement_csv",
    ),
    path(
        "management/customers/<int:pk>/payments/new/",
        views.customer_record_payment,
        name="customer_record_payment",
    ),
    path(
        "management/customers/<int:pk>/adjustments/new/",
        views.customer_record_adjustment,
        name="customer_record_adjustment",
    ),
    path(
        "management/customers/<int:pk>/ledger/<int:ledger_id>/delete/",
        views.customer_ledger_delete,
        name="customer_ledger_delete",
    ),
    path(
        "management/customers/<int:pk>/payments/<int:payment_id>/delete/",
        views.customer_payment_delete,
        name="customer_payment_delete",
    ),
    path(
        "management/customers/<int:pk>/delete/",
        views.customer_delete,
        name="customer_delete",
    ),
    path(
        "management/customers/<int:pk>/write-off/",
        views.customer_write_off,
        name="customer_write_off",
    ),
    path(
        "management/customers/<int:pk>/phones/new/",
        views.customer_add_phone,
        name="customer_add_phone",
    ),
    path(
        "management/customers/<int:pk>/phones/<int:phone_id>/delete/",
        views.customer_remove_phone,
        name="customer_remove_phone",
    ),
    path("employee/api/customers/lookup/", views.api_customer_lookup, name="api_customer_lookup"),
    path("employee/api/customers/create/", views.api_customer_create, name="api_customer_create"),
]
