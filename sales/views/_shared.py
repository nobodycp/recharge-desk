"""Helpers shared by every view in the sales package.

These wrap the small HTMX response conventions the front-end relies on
so that each individual view stays focused on the business action.
"""

import json
from urllib.parse import urlsplit

from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from sales.employee_editing import sale_is_employee_editable
from sales.models import Sale

# One sale rendered alone: the employee "My entries" row, or the management
# list's desktop row / mobile card (``?layout=row|card``).
SALE_ROW_TEMPLATES = {
    "employee": "sales/partials/employee_recent_sale_row.html",
    "row": "sales/partials/management_sale_row.html",
    "card": "sales/partials/management_sale_card.html",
}


def is_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


def sale_row_queryset():
    return Sale.objects.select_related(
        "company",
        "product",
        "product__line",
        "payment_method",
        "customer",
        "created_by",
        "employee_recipient",
        "employee_recipient__user",
        "employee_recipient__user__profile",
    )


def sale_row_layout(request, *, management: bool) -> str:
    layout = request.GET.get("layout") or "employee"
    if layout not in SALE_ROW_TEMPLATES or not management:
        return "employee"
    return layout


def safe_return_url(request, url: str, fallback: str) -> str:
    """``url`` when it points back at this site, else ``fallback``."""
    if url and url_has_allowed_host_and_scheme(
        url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return url
    return fallback


def render_sale_row(request, sale: Sale, layout: str) -> HttpResponse:
    ctx = {"s": sale, "row_layout": layout}
    if layout == "employee":
        ctx["editable_sale_ids"] = (
            {sale.pk} if sale_is_employee_editable(sale, request.user) else set()
        )
    else:
        # Row actions link back to the list page the row sits in, not this endpoint.
        current = urlsplit(request.headers.get("HX-Current-URL", ""))
        current_path = current.path + (f"?{current.query}" if current.query else "")
        ctx["list_path"] = safe_return_url(
            request, current_path, reverse("sales:management_sale_list")
        )
    return render(request, SALE_ROW_TEMPLATES[layout], ctx)


def htmx_remove_target() -> HttpResponse:
    """Empty 200 body so htmx swaps an empty string into the target (removing it).

    NOTE: We deliberately avoid 204 here — htmx skips the swap on 204 by default,
    which would leave the row visually stuck even though the backend succeeded.
    """
    return HttpResponse("", status=200)


def htmx_action_error(message: str, status: int = 200) -> HttpResponse:
    """Tell htmx to leave the row in place and surface the error to the user.

    Status 200 with HX-Reswap=none keeps the target untouched while still
    delivering the HX-Trigger event for the toast/alert. We do not use a
    non-2xx code because htmx treats those as transport errors and may
    suppress the trigger depending on configuration.
    """
    resp = HttpResponse("", status=status)
    resp["HX-Reswap"] = "none"
    resp["HX-Trigger"] = json.dumps({"rdSaleActionError": message or "Action failed"})
    return resp


# Backward-compatible aliases — keep the underscored names other modules
# (notably `customers.views`) already import.
_is_htmx = is_htmx
_htmx_remove_target = htmx_remove_target
_htmx_action_error = htmx_action_error
