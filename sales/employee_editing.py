"""Rules for employee self-service edit/delete on recent sales."""

from __future__ import annotations

from sales.models import Sale

EMPLOYEE_RECENT_EDIT_LIMIT = 10


def employee_editable_sale_ids(user) -> set[int]:
    """PKs of the employee's last N non-cancelled sales (newest first)."""
    if not user or not getattr(user, "is_authenticated", False):
        return set()
    return set(
        Sale.objects.filter(created_by=user)
        .exclude(status__in=(Sale.Status.CANCELLED, Sale.Status.WRITTEN_OFF))
        .order_by("-created_at", "-id")
        .values_list("pk", flat=True)[:EMPLOYEE_RECENT_EDIT_LIMIT]
    )


def sale_is_employee_editable(sale: Sale, user, editable_ids: set[int] | None = None) -> bool:
    if sale.created_by_id != user.id:
        return False
    if sale.status in (Sale.Status.CANCELLED, Sale.Status.WRITTEN_OFF):
        return False
    if editable_ids is None:
        editable_ids = employee_editable_sale_ids(user)
    return sale.pk in editable_ids
