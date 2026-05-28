from django.urls import path

from employees import views

app_name = "employees"

urlpatterns = [
    path("employees/", views.employee_list, name="employee_list"),
    path("employees/new/", views.employee_create, name="employee_create"),
    path("employees/<int:pk>/", views.employee_detail, name="employee_detail"),
    path("employees/<int:pk>/edit/", views.employee_edit, name="employee_edit"),
    path(
        "employees/accrue-salaries/",
        views.employee_run_salary_accrual,
        name="employee_run_salary_accrual",
    ),
]
