from django.urls import path

from companies import views

app_name = "companies"

urlpatterns = [
    path("management/companies/", views.company_list, name="company_list"),
    path("management/companies/<int:pk>/", views.company_detail, name="company_detail"),
    path(
        "management/companies/<int:pk>/layan-reconcile/",
        views.layan_reconcile,
        name="layan_reconcile",
    ),
    path("management/companies/new/", views.company_create, name="company_create"),
    path("management/companies/<int:pk>/edit/", views.company_edit, name="company_edit"),
    path("management/companies/<int:pk>/delete/", views.company_delete, name="company_delete"),
    path("management/products/", views.product_list, name="product_list"),
    path("management/products/lines/new/", views.product_line_create, name="product_line_create"),
    path("management/products/lines/<int:pk>/edit/", views.product_line_edit, name="product_line_edit"),
    path("management/products/lines/<int:pk>/delete/", views.product_line_delete, name="product_line_delete"),
    path(
        "management/products/lines/<int:line_pk>/packages/new/",
        views.product_variant_create,
        name="product_variant_create",
    ),
    path("management/products/packages/<int:pk>/edit/", views.product_variant_edit, name="product_variant_edit"),
    path("management/products/packages/<int:pk>/delete/", views.product_variant_delete, name="product_variant_delete"),
]
