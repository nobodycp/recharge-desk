from django.db import models
from django.utils.translation import gettext_lazy as _


class PhoneLineTracking(models.Model):
    """Per-line metadata for the stale-number report (SIM label, dismiss-from-list)."""

    reference_key = models.CharField(
        _("normalized reference"),
        max_length=64,
        unique=True,
        db_index=True,
        help_text=_("Lowercased trimmed sale reference / phone key."),
    )
    sim_identifier = models.CharField(_("SIM / chip"), max_length=200, blank=True)
    last_display_reference = models.CharField(
        _("last display reference"),
        max_length=64,
        blank=True,
        help_text=_("Most recent raw reference as entered on a sale."),
    )
    is_dismissed = models.BooleanField(_("hidden from stale list"), default=False)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("phone line tracking")
        verbose_name_plural = _("phone line tracking")

    def __str__(self):
        return self.reference_key
