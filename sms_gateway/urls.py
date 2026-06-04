from django.urls import path

from sms_gateway import views

app_name = "sms_gateway"

urlpatterns = [
    # Device-facing API lives in ``api_urls.py`` (mounted outside i18n so the
    # machine endpoints are not locale-redirected). Management surfaces below.
    path("management/sms-gateway/settings/", views.settings_index, name="settings"),
    path("management/sms-gateway/settings/general/save/", views.settings_general_save, name="settings_general_save"),
    path("management/sms-gateway/settings/api/save/", views.api_gateway_save, name="api_gateway_save"),
    path("management/sms-gateway/settings/api/test/", views.api_gateway_test, name="api_gateway_test"),
    path("management/sms-gateway/settings/simulate/", views.inbound_simulate, name="inbound_simulate"),
    path("management/sms-gateway/settings/replies/save/", views.reply_policy_save, name="reply_policy_save"),
    path("management/sms-gateway/settings/devices/new/", views.device_create, name="device_create"),
    path("management/sms-gateway/settings/devices/<int:pk>/edit/", views.device_update, name="device_update"),
    path("management/sms-gateway/settings/devices/<int:pk>/regenerate/", views.device_regenerate_token, name="device_regenerate_token"),
    path("management/sms-gateway/settings/devices/<int:pk>/reactivate/", views.device_reactivate, name="device_reactivate"),
    path("management/sms-gateway/settings/devices/<int:pk>/delete/", views.device_delete, name="device_delete"),
    path("management/sms-gateway/settings/access-rules/new/", views.access_rule_create, name="access_rule_create"),
    path("management/sms-gateway/settings/access-rules/<int:pk>/delete/", views.access_rule_delete, name="access_rule_delete"),
    path("management/sms-gateway/reports/", views.reports_list, name="reports"),
    path("management/sms-gateway/reports/<int:pk>/resend/", views.outbound_resend, name="outbound_resend"),
]
