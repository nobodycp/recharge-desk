from __future__ import annotations

from django.urls import path

from providers.api import views

app_name = "api_v1"

urlpatterns = [
    path("refresh/", views.refresh_submit, name="refresh"),
    path("refresh/<uuid:job_id>/", views.refresh_status, name="refresh_status"),
]
