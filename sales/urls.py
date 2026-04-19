from django.urls import path

from sales import views

app_name = "sales"

urlpatterns = [
    path("employee/sales/", views.employee_entry, name="employee_entry"),
    path(
        "employee/sales/api/payer-by-number/",
        views.api_payer_by_number,
        name="api_payer_by_number",
    ),
    path(
        "employee/sales/api/payer-name-suggestions/",
        views.api_payer_name_suggestions,
        name="api_payer_name_suggestions",
    ),
    path("employee/sales/products/", views.employee_product_fragment, name="employee_product_fragment"),
    path("management/sales/", views.management_sale_list, name="management_sale_list"),
    path("management/sales/bulk-mark-paid/", views.bulk_sales_mark_paid, name="bulk_sales_mark_paid"),
    path("management/sales/pending/", views.pending_payments, name="pending_payments"),
    path("management/sales/awaiting/", views.awaiting_approvals, name="awaiting_approvals"),
    path("management/sales/<int:pk>/approve/", views.sale_approve, name="sale_approve"),
    path("management/sales/<int:pk>/reject/", views.sale_reject, name="sale_reject"),
    path("management/sales/<int:pk>/mark-paid/", views.sale_mark_paid, name="sale_mark_paid"),
    path("management/sales/<int:pk>/edit/", views.sale_edit, name="sale_edit"),
    path("management/sales/<int:pk>/cancel/", views.sale_cancel, name="sale_cancel"),
    path("management/sales/<int:pk>/delete/", views.sale_delete_permanent, name="sale_delete_permanent"),
    path("management/payment-methods/", views.payment_method_list, name="payment_method_list"),
    path("management/payment-methods/new/", views.payment_method_create, name="payment_method_create"),
    path("management/payment-methods/<int:pk>/edit/", views.payment_method_edit, name="payment_method_edit"),
]
