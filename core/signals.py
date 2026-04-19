"""Cross-app signals owned by core.

Currently only one job: bump the dashboard KPI cache version every
time a row in one of the financially-relevant tables is created,
updated or deleted. Centralising the receivers here means the rest of
the codebase doesn't need to know caching exists — services and the
admin both naturally trigger the invalidation through model saves.
"""

from __future__ import annotations

from django.apps import apps
from django.db.models.signals import post_delete, post_save

from core.kpi_cache import bump_kpi_version

# Tables whose changes affect at least one dashboard KPI. Adding to
# this list is the only step needed when a new revenue/cost source is
# introduced.
TRACKED_MODELS = (
    ("sales", "Sale"),
    ("sales", "CompanyBalanceTransaction"),
    ("expenses", "Expense"),
    ("customers", "CustomerLedger"),
    ("customers", "CustomerPayment"),
)


def _bump(*_args, **_kwargs):
    bump_kpi_version()


def _connect():
    for app_label, model_name in TRACKED_MODELS:
        model = apps.get_model(app_label, model_name)
        # dispatch_uid prevents duplicate registration if
        # AppConfig.ready() is re-entered (e.g. autoreload during dev).
        post_save.connect(
            _bump,
            sender=model,
            dispatch_uid=f"kpi-bump-save-{app_label}.{model_name}",
            weak=False,
        )
        post_delete.connect(
            _bump,
            sender=model,
            dispatch_uid=f"kpi-bump-delete-{app_label}.{model_name}",
            weak=False,
        )


_connect()
