"""JSON endpoints used by the cashier's sales-entry screen.

Today these are the only customer-facing employee routes — the rest of
the customers app is management-only. Kept in their own module so
adding more API endpoints later doesn't bloat the page-rendering files.
"""

from __future__ import annotations

from django.db.models import Q
from django.http import JsonResponse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET, require_POST

from accounts.permissions import employee_required
from customers.models import Customer
from customers.services import create_customer


@employee_required
@require_GET
def api_customer_lookup(request):
    """Typeahead for the entry form: match name or phone, return id+name+balance."""
    q = (request.GET.get("q") or "").strip()
    if len(q) < 2:
        return JsonResponse({"results": []})
    qs = (
        Customer.objects.filter(is_active=True)
        .filter(Q(name__icontains=q) | Q(phones__phone__icontains=q))
        .distinct()
        .order_by("name")[:10]
    )
    results = [
        {
            "id": c.pk,
            "name": c.name,
            "balance": str(c.current_balance),
        }
        for c in qs
    ]
    return JsonResponse({"results": results})


@employee_required
@require_POST
def api_customer_create(request):
    """Inline create from the entry form. Returns JSON id+name."""
    name = (request.POST.get("name") or "").strip()
    phone = (request.POST.get("phone") or "").strip()
    if not name:
        return JsonResponse(
            {"ok": False, "error": str(_("Name is required."))}, status=400
        )
    try:
        customer = create_customer(
            name=name,
            phones=[phone] if phone else None,
            user=request.user,
        )
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True, "id": customer.pk, "name": customer.name})
