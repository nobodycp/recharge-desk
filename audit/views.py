"""Management-only viewer for the audit log."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.shortcuts import render
from django.utils.dateparse import parse_date
from django.utils.translation import gettext_lazy as _

from accounts.permissions import management_required
from audit.models import AuditAction, AuditLog
from core.pagination import paginate_request

User = get_user_model()


@management_required
def audit_log_list(request):
    qs = AuditLog.objects.select_related("actor").order_by("-created_at", "-id")

    action = (request.GET.get("action") or "").strip()
    if action and action in AuditAction.values:
        qs = qs.filter(action=action)

    model_label = (request.GET.get("model") or "").strip().lower()
    if model_label:
        qs = qs.filter(model_label=model_label)

    actor_id = (request.GET.get("actor") or "").strip()
    if actor_id.isdigit():
        qs = qs.filter(actor_id=int(actor_id))

    date_from = parse_date(request.GET.get("date_from") or "")
    date_to = parse_date(request.GET.get("date_to") or "")
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(object_repr__icontains=q) | Q(object_id__iexact=q))

    page_obj = paginate_request(request, qs)

    actors = (
        User.objects.filter(audit_events__isnull=False)
        .distinct()
        .order_by("username")
        .values("id", "username")
    )
    models_seen = (
        AuditLog.objects.values_list("model_label", flat=True)
        .distinct()
        .order_by("model_label")
    )

    return render(
        request,
        "audit/audit_log_list.html",
        {
            "page_obj": page_obj,
            "actors": list(actors),
            "models_seen": list(models_seen),
            "action_choices": AuditAction.choices,
            "title": _("Audit log"),
        },
    )
