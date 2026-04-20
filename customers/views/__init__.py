"""Customers views package.

Split mirrors the layout used by :mod:`sales.views`:

* :mod:`customers.views.crud` — list / create / edit / detail / delete.
* :mod:`customers.views.actions` — POST-only buttons on the detail
  page (record payment, manual adjustment, write off, delete payment,
  delete ledger row, add/remove phone).
* :mod:`customers.views.api` — JSON endpoints called from the cashier's
  sales-entry screen.
* :mod:`customers.views._shared` — small helpers (currently form-error
  flashing).

This ``__init__`` re-exports every public view, so ``customers.urls``
(and any future ``from customers import views`` callers) keep working
with no changes.
"""

from customers.views.actions import (
    customer_add_phone,
    customer_ledger_delete,
    customer_payment_delete,
    customer_record_adjustment,
    customer_record_payment,
    customer_remove_phone,
    customer_write_off,
)
from customers.views.api import api_customer_create, api_customer_lookup
from customers.views.exports import (
    customer_payments_export_csv,
    customers_export_csv,
)
from customers.views.statements import (
    customer_detailed_invoice,
    customer_statement,
    customer_statement_csv,
)
from customers.views.crud import (
    customer_create,
    customer_delete,
    customer_detail,
    customer_edit,
    customer_list,
)

__all__ = [
    # crud
    "customer_list",
    "customer_create",
    "customer_edit",
    "customer_detail",
    "customer_delete",
    # actions
    "customer_record_payment",
    "customer_record_adjustment",
    "customer_write_off",
    "customer_payment_delete",
    "customer_ledger_delete",
    "customer_add_phone",
    "customer_remove_phone",
    # api
    "api_customer_lookup",
    "api_customer_create",
    # exports
    "customers_export_csv",
    "customer_payments_export_csv",
    # statements
    "customer_statement",
    "customer_statement_csv",
    "customer_detailed_invoice",
]
