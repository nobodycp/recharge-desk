"""Company list ordering."""

COMPANY_SORT_WHITELIST = {
    "name": "name",
    "balance": "current_balance",
    "opening": "opening_balance",
    "active": "is_active",
}


def apply_company_list_ordering(request, queryset, *, sort_param="sort", order_param="order"):
    sort = (request.GET.get(sort_param) or "name").strip()
    order = (request.GET.get(order_param) or "asc").lower()
    field = COMPANY_SORT_WHITELIST.get(sort, "name")
    if order not in ("asc", "desc"):
        order = "asc"
    prefix = "" if order == "asc" else "-"
    return queryset.order_by(f"{prefix}{field}", f"{prefix}pk")
