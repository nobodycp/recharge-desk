"""Expense list ordering."""

EXPENSE_SORT_WHITELIST = {
    "date": "date",
    "amount": "amount",
    "title": "title",
    "category": "category",
}


def apply_expense_list_ordering(request, queryset, *, sort_param="sort", order_param="order"):
    sort = (request.GET.get(sort_param) or "date").strip()
    order = (request.GET.get(order_param) or "desc").lower()
    field = EXPENSE_SORT_WHITELIST.get(sort, "date")
    if order not in ("asc", "desc"):
        order = "desc"
    prefix = "" if order == "asc" else "-"
    return queryset.order_by(f"{prefix}{field}", f"{prefix}pk")
