"""Audit log: append-only record of who changed what and when.

Each row captures a single semantic action (create / update / delete /
approve / cancel / mark paid / pay / adjust / write off …) against a
specific Django model instance. The ``changes`` JSON column lets
auditors see what fields were touched and what their old/new values
were; for deletes we also snapshot the row's display string so the
record stays meaningful after the underlying object is gone.

Rows are intentionally tiny (a few hundred bytes each) and the table
has no hot-path writers other than service-layer helpers; an index on
``(action, -created_at)`` and on ``(model_label, object_id)`` is
enough to keep the management viewer responsive.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class AuditAction(models.TextChoices):
    CREATE = "create", _("Create")
    UPDATE = "update", _("Update")
    DELETE = "delete", _("Delete")
    APPROVE = "approve", _("Approve")
    REJECT = "reject", _("Reject")
    CANCEL = "cancel", _("Cancel")
    MARK_PAID = "mark_paid", _("Mark paid")
    PAY = "pay", _("Record payment")
    ADJUST = "adjust", _("Adjust balance")
    WRITE_OFF = "write_off", _("Write off")


class AuditLog(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
        verbose_name=_("actor"),
    )
    action = models.CharField(
        _("action"),
        max_length=20,
        choices=AuditAction.choices,
    )
    model_label = models.CharField(
        _("model"),
        max_length=80,
        help_text=_("Lowercased ``app_label.model_name`` of the affected row."),
    )
    object_id = models.CharField(
        _("object id"),
        max_length=64,
        blank=True,
        default="",
    )
    object_repr = models.CharField(
        _("object"),
        max_length=255,
        blank=True,
        default="",
        help_text=_("Snapshot of ``str(instance)`` so deletes stay meaningful."),
    )
    changes = models.JSONField(
        _("changes"),
        default=dict,
        blank=True,
        help_text=_("Per-field {old, new} for updates; arbitrary context otherwise."),
    )
    ip = models.GenericIPAddressField(_("IP address"), null=True, blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        verbose_name = _("audit log entry")
        verbose_name_plural = _("audit log")
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["-created_at"], name="audit_recent_idx"),
            models.Index(fields=["action", "-created_at"], name="audit_action_idx"),
            models.Index(fields=["model_label", "object_id"], name="audit_target_idx"),
            models.Index(fields=["actor", "-created_at"], name="audit_actor_idx"),
        ]

    def __str__(self):
        actor = self.actor.username if self.actor_id else "—"
        return f"[{self.created_at:%Y-%m-%d %H:%M}] {actor} {self.action} {self.model_label}#{self.object_id}"
