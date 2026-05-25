"""View modules for the phone_refresh app.

Re-export the callables so ``phone_refresh.urls`` can keep its import
list flat.
"""
from phone_refresh.views.api import (
    api_index,
    api_live_test,
    api_settings_save,
    api_token_create,
    api_token_delete,
    api_token_revoke,
)
from phone_refresh.views.general import (
    settings_general_save,
    settings_internal_test,
)
from phone_refresh.views.public import public_refresh_api, public_refresh_page
from phone_refresh.views.settings import (
    message_create,
    message_delete,
    message_edit,
    providers_general_save,
    providers_index,
    providers_test,
    report_log_detail,
    report_logs_bulk_delete,
    reports_list,
    rule_create,
    rule_delete,
    rule_edit,
    settings_index,
    status_create,
    status_delete,
    status_edit,
)

__all__ = [
    "settings_index",
    "providers_index",
    "providers_general_save",
    "providers_test",
    "settings_general_save",
    "settings_internal_test",
    "rule_create",
    "rule_edit",
    "rule_delete",
    "message_create",
    "message_edit",
    "message_delete",
    "status_create",
    "status_edit",
    "status_delete",
    "reports_list",
    "report_log_detail",
    "report_logs_bulk_delete",
    "public_refresh_page",
    "public_refresh_api",
    "api_index",
    "api_settings_save",
    "api_token_create",
    "api_token_revoke",
    "api_token_delete",
    "api_live_test",
]
