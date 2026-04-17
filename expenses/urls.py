from django.urls import path

from expenses import views

app_name = "expenses"

urlpatterns = [
    path("management/expenses/", views.expense_list, name="expense_list"),
    path("management/expenses/new/", views.expense_create, name="expense_create"),
    path("management/expenses/<int:pk>/edit/", views.expense_edit, name="expense_edit"),
    path(
        "management/expenses/<int:pk>/delete/",
        views.expense_delete,
        name="expense_delete",
    ),
    path("management/reports/expenses/", views.expense_report, name="expense_report"),
]
