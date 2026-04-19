from django.urls import path

from customers import views

app_name = "customers"

urlpatterns = [
    path("management/customers/", views.customer_list, name="customer_list"),
    path("management/customers/new/", views.customer_create, name="customer_create"),
    path("management/customers/<int:pk>/", views.customer_detail, name="customer_detail"),
    path("management/customers/<int:pk>/edit/", views.customer_edit, name="customer_edit"),
    path(
        "management/customers/<int:pk>/payments/new/",
        views.customer_record_payment,
        name="customer_record_payment",
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
