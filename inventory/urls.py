from django.urls import path

from inventory import views

app_name = "inventory"

urlpatterns = [
    path("management/inventory/", views.inventory_overview, name="overview"),
    path(
        "management/inventory/lines/<int:line_id>/init-main/",
        views.inventory_overview_init_main,
        name="overview_init_main",
    ),
    path(
        "management/inventory/lines/<int:line_id>/",
        views.inventory_line_detail,
        name="line_detail",
    ),
    path("management/inventory/main/", views.inventory_main, name="main"),
    path(
        "management/inventory/main/<int:pk>/set/",
        views.inventory_main_set,
        name="main_set",
    ),
    path(
        "management/inventory/main/<int:pk>/adjust/",
        views.inventory_main_adjust,
        name="main_adjust",
    ),
    path(
        "management/inventory/main/<int:pk>/damaged/",
        views.inventory_main_damaged,
        name="main_damaged",
    ),
    path(
        "management/inventory/main/<int:pk>/clear/",
        views.inventory_main_clear,
        name="main_clear",
    ),
    path(
        "management/inventory/main/<int:pk>/delete/",
        views.inventory_main_delete,
        name="main_delete",
    ),
    path("management/inventory/customers/", views.inventory_customers, name="customers"),
    path(
        "management/inventory/customers/<int:pk>/",
        views.inventory_customer_detail,
        name="customer_detail",
    ),
    path(
        "management/inventory/customers/<int:pk>/balances/<int:balance_pk>/set/",
        views.inventory_customer_set,
        name="customer_set",
    ),
    path(
        "management/inventory/customers/<int:pk>/balances/<int:balance_pk>/adjust/",
        views.inventory_customer_adjust,
        name="customer_adjust",
    ),
    path(
        "management/inventory/customers/<int:pk>/balances/<int:balance_pk>/damaged/",
        views.inventory_customer_damaged,
        name="customer_damaged",
    ),
    path(
        "management/inventory/customers/<int:pk>/balances/<int:balance_pk>/clear/",
        views.inventory_customer_clear,
        name="customer_clear",
    ),
    path(
        "management/inventory/customers/<int:pk>/balances/<int:balance_pk>/delete/",
        views.inventory_customer_delete,
        name="customer_delete",
    ),
    path("management/inventory/movements/", views.inventory_movements, name="movements"),
    path("management/inventory/cards/", views.inventory_cards, name="cards"),
    path(
        "management/inventory/movements/<int:pk>/delete/",
        views.inventory_movement_delete,
        name="movement_delete",
    ),
    path("employee/inventory/", views.inventory_overview, name="employee_overview"),
    path("employee/inventory/main/", views.inventory_main, name="employee_main"),
    path(
        "employee/inventory/lines/<int:line_id>/",
        views.inventory_line_detail,
        name="employee_line_detail",
    ),
    path("employee/inventory/customers/", views.inventory_customers, name="employee_customers"),
    path(
        "employee/inventory/customers/<int:pk>/",
        views.inventory_customer_detail,
        name="employee_customer_detail",
    ),
    path("employee/inventory/movements/", views.inventory_movements, name="employee_movements"),
    path("employee/inventory/cards/", views.inventory_cards, name="employee_cards"),
]
