"""Device-facing API routes.

These are mounted OUTSIDE ``i18n_patterns`` (see ``config/urls.py``) so the
machine endpoints have stable, locale-free URLs. A gateway phone must never be
302-redirected to ``/ar/…`` (a redirect would turn its ``POST`` into a ``GET``
and drop the body). The management/UI routes stay localized in ``urls.py``.
"""
from django.urls import path

from sms_gateway import views

app_name = "sms_gateway_api"

urlpatterns = [
    path("sms-gateway/api/inbound/", views.sms_inbound, name="api_inbound"),
    path("sms-gateway/api/outbox/", views.sms_outbox, name="api_outbox"),
    path("sms-gateway/api/delivery/", views.sms_delivery, name="api_delivery"),
]
