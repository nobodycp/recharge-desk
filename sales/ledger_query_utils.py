"""Ordering for company balance ledger listings."""

from django.db.models import Q

LEDGER_SORT_WHITELIST = {
    "created_at": "created_at",
    "amount": "amount",
    "entry_type": "entry_type",
    "reference_id": "reference_id",
    "by": "created_by__username",
}


def apply_ledger_list_ordering(request, queryset, *, sort_param="ledger_sort", order_param="ledger_order"):
    sort = (request.GET.get(sort_param) or "created_at").strip()
    order = (request.GET.get(order_param) or "desc").lower()
    field = LEDGER_SORT_WHITELIST.get(sort, "created_at")
    if order not in ("asc", "desc"):
        order = "desc"
    prefix = "" if order == "asc" else "-"
    return queryset.order_by(f"{prefix}{field}", f"{prefix}pk")


def filter_ledger_queryset(qs, q: str):
    q = (q or "").strip()
    if not q:
        return qs
    parts = Q(notes__icontains=q)
    if q.isdigit():
        parts |= Q(reference_id=int(q))
    return qs.filter(parts)
