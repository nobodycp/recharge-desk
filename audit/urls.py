from django.urls import path

from audit import views

app_name = "audit"

urlpatterns = [
    path("management/audit/", views.audit_log_list, name="audit_log_list"),
]
