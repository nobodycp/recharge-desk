from __future__ import annotations

from django.urls import path, re_path

from providers.api import views

app_name = "api_v1"

urlpatterns = [
    path("refresh/", views.refresh_submit, name="refresh"),
    path("refresh/<uuid:job_id>/", views.refresh_status, name="refresh_status"),
    # Keep last: anything else under api/v1/ (unknown route, malformed job
    # id, no trailing slash) answers JSON 404.
    re_path(r"^.*$", views.not_found, name="not_found"),
]
