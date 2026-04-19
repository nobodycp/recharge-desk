from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.image_utils import maybe_optimize_image_field


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
    avatar = models.ImageField(
        _("avatar"),
        upload_to="avatars/",
        blank=True,
        null=True,
        help_text=_("Profile picture shown in the topbar menu."),
    )

    class Meta:
        verbose_name = _("user profile")
        verbose_name_plural = _("user profiles")

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"

    def save(self, *args, **kwargs):
        # Re-encode any freshly uploaded avatar as a small WebP (same
        # pipeline as Company / ProductLine icons) so a 5 MB phone snap
        # doesn't bloat the topbar payload.
        maybe_optimize_image_field(self, "avatar")
        super().save(*args, **kwargs)

    @property
    def display_name(self) -> str:
        """Best-effort human label: full_name → first/last name → username."""
        if self.full_name:
            return self.full_name
        u = self.user
        full = (u.get_full_name() or "").strip()
        return full or u.username

    @property
    def initials(self) -> str:
        """Two-letter monogram for the placeholder avatar."""
        source = self.display_name.strip() or self.user.username
        parts = [p for p in source.split() if p]
        if len(parts) >= 2:
            return (parts[0][:1] + parts[-1][:1]).upper()
        return source[:2].upper()
