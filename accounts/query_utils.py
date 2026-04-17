"""User profile list ordering."""

USER_SORT_WHITELIST = {
    "username": "user__username",
    "name": "full_name",
    "role": "role",
    "active": "user__is_active",
}


def apply_user_list_ordering(request, queryset, *, sort_param="sort", order_param="order"):
    sort = (request.GET.get(sort_param) or "username").strip()
    order = (request.GET.get(order_param) or "asc").lower()
    field = USER_SORT_WHITELIST.get(sort, "user__username")
    if order not in ("asc", "desc"):
        order = "asc"
    prefix = "" if order == "asc" else "-"
    return queryset.order_by(f"{prefix}{field}", f"{prefix}pk")
