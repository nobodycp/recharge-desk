"""Bulk actions on multiple sales (currently: mark a batch as paid)."""

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from accounts.permissions import management_required
from sales.models import Sale
from sales.services import mark_sale_paid


@management_required
@require_POST
def bulk_sales_mark_paid(request):
    ids = []
    for x in request.POST.getlist("sale_ids"):
        try:
            ids.append(int(x))
        except (TypeError, ValueError):
            continue
    next_url = (request.POST.get("next") or "").strip() or reverse("sales:management_sale_list")
    if not ids:
        messages.warning(request, _("No sales selected."))
    else:
        qs = Sale.objects.filter(pk__in=ids, status=Sale.Status.PENDING)
        ok = 0
        failed = 0
        for sale in qs:
            try:
                mark_sale_paid(sale=sale, user=request.user)
                ok += 1
            except ValueError:
                failed += 1
        if ok:
            messages.success(request, _("Marked %(n)s sale(s) as paid.") % {"n": ok})
        if failed:
            messages.warning(
                request,
                _("%(n)s sale(s) could not be updated (already settled or on-account).")
                % {"n": failed},
            )
        if not ok and not failed:
            messages.info(request, _("No pending sales were updated."))
    if request.headers.get("HX-Request"):
        r = HttpResponse(status=204)
        r["HX-Redirect"] = next_url
        return r
    return redirect(next_url)
