"""Helpers shared by every view in the sales package.

These wrap the small HTMX response conventions the front-end relies on
so that each individual view stays focused on the business action.
"""

import json

from django.http import HttpResponse


def is_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


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
