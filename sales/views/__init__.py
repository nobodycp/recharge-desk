"""Sales views package.

The sales surface is large enough that a single ``views.py`` got hard to
navigate, so it is split into focused submodules:

* :mod:`sales.views.employee` — entry form and JSON helpers used by
  the cashier's screen.
* :mod:`sales.views.management` — list/queue pages plus the per-sale
  lifecycle actions (approve, reject, mark paid, cancel, edit, delete).
* :mod:`sales.views.payment_methods` — CRUD for the PaymentMethod
  taxonomy.
* :mod:`sales.views.bulk` — multi-row actions (currently bulk
  mark-as-paid).
* :mod:`sales.views._shared` — small HTMX response helpers.

This ``__init__`` re-exports every public view so callers (urls.py,
external imports, tests) keep working without changes.
"""

from sales.views._shared import (
    _htmx_action_error,
    _htmx_remove_target,
    _is_htmx,
    htmx_action_error,
    htmx_remove_target,
    is_htmx,
)
from sales.views.bulk import bulk_sales_mark_paid
from sales.views.employee import (
    api_payer_by_number,
    api_payer_name_suggestions,
    employee_entry,
    employee_product_fragment,
)
from sales.views.management import (
    awaiting_approvals,
    management_sale_list,
    pending_payments,
    sale_approve,
    sale_cancel,
    sale_delete_permanent,
    sale_edit,
    sale_mark_paid,
    sale_reject,
)
from sales.views.payment_methods import (
    payment_method_create,
    payment_method_edit,
    payment_method_list,
)

__all__ = [
    # employee
    "employee_entry",
    "employee_product_fragment",
    "api_payer_by_number",
    "api_payer_name_suggestions",
    # management
    "management_sale_list",
    "pending_payments",
    "awaiting_approvals",
    "sale_approve",
    "sale_reject",
    "sale_mark_paid",
    "sale_cancel",
    "sale_edit",
    "sale_delete_permanent",
    # payment methods
    "payment_method_list",
    "payment_method_create",
    "payment_method_edit",
    # bulk
    "bulk_sales_mark_paid",
    # htmx helpers (legacy underscored names kept for backward compat)
    "is_htmx",
    "htmx_remove_target",
    "htmx_action_error",
    "_is_htmx",
    "_htmx_remove_target",
    "_htmx_action_error",
]
