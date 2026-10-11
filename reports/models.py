from django.db import models
from django.utils.translation import gettext_lazy as _


class PhoneLineTracking(models.Model):
    """Per-line metadata for the phone statement (SIM label, virtual 1-day renew)."""

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
    # Virtual “1-day charge” without creating a Sale. When newer than the last
    # real sale, lifecycle math uses this timestamp + 1 day duration.
    manual_reset_at = models.DateTimeField(
        _("virtual 1-day renew at"),
        null=True,
        blank=True,
        help_text=_(
            "Set when an overdue line is renewed as a 1-day charge so it leaves "
            "the inactivity threshold for another period."
        ),
    )
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("phone line tracking")
        verbose_name_plural = _("phone line tracking")

    def __str__(self):
        return self.reference_key
