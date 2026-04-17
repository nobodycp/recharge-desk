from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class UserProfile(models.Model):
    class Role(models.TextChoices):
        MANAGEMENT = "management", _("Management")
        EMPLOYEE = "employee", _("Employee")

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    full_name = models.CharField(_("full name"), max_length=200, blank=True)
    role = models.CharField(
        _("role"),
        max_length=20,
        choices=Role.choices,
        default=Role.MANAGEMENT,
    )
    is_active_profile = models.BooleanField(_("profile active"), default=True)

    class Meta:
        verbose_name = _("user profile")
        verbose_name_plural = _("user profiles")

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"
