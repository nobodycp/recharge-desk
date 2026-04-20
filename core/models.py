from django.db import models
from django.utils.translation import gettext_lazy as _

from core.image_utils import maybe_optimize_image_field

# Kept as named constants for any external code that may still import
# them; the model itself no longer caches so updates from the admin
# panel are reflected on the very next request, even when Django runs
# under a multi-worker WSGI server (gunicorn) where each worker has
# its own ``LocMemCache`` and per-process invalidation isn't enough.
SITE_BRANDING_CACHE_KEY = "core:site_branding:singleton"
SITE_BRANDING_CACHE_TTL = 0


class SiteBranding(models.Model):
    """Project-wide branding (logo, etc.) — one row, edited by management.

    The model intentionally enforces a single row (``pk=1``) so the rest
    of the codebase can fetch it via :py:meth:`load` without worrying
    about multiplicity. The uploaded logo is run through the same WebP
    optimizer used for company icons, capped at 384px on its longest
    side, so a multi-megabyte upload doesn't bloat the public login
    page.
    """

    # Big enough to stay sharp at the displayed login-brand size on a 2x
    # retina screen (~352px logical → ~704px physical) while keeping the
    # WebP under a few dozen KB.
    LOGO_MAX_SIZE = 768
    # Browsers render the favicon at 16-32px, but high-DPI tabs and
    # bookmark bars can request up to ~96px. 256px gives plenty of
    # headroom while keeping the WebP tiny.
    FAVICON_MAX_SIZE = 256

    site_name = models.CharField(
        _("site name"),
        max_length=120,
        blank=True,
        help_text=_(
            "Displayed in the browser tab title, the sidebar header, and"
            " the login page. Leave empty to keep the default name."
        ),
    )
    tagline = models.CharField(
        _("tagline"),
        max_length=160,
        blank=True,
        help_text=_(
            "Short text shown under the site name in the sidebar. Leave"
            " empty to keep the default."
        ),
    )
    logo = models.ImageField(
        _("logo"),
        upload_to="branding/",
        blank=True,
        null=True,
        help_text=_("Shown above the login form. Resized automatically."),
    )
    favicon = models.ImageField(
        _("favicon"),
        upload_to="branding/",
        blank=True,
        null=True,
        help_text=_("Shown in the browser tab and bookmarks. Resized automatically."),
    )
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("site branding")
        verbose_name_plural = _("site branding")

    def __str__(self) -> str:
        return "Site branding"

    def save(self, *args, **kwargs):
        # Pin to a single row so callers never need to pick "the right" one.
        self.pk = 1
        # ``trim=True`` strips transparent / solid-color borders so a wordmark
        # exported on a 2000×600 canvas doesn't render as a tiny blob with
        # huge empty margins on the login page.
        maybe_optimize_image_field(self, "logo", max_size=self.LOGO_MAX_SIZE, trim=True)
        # The favicon is square and tiny — trim the surrounding padding
        # too so a logo exported on a wide canvas doesn't render as a
        # speck at the corner of the tab.
        maybe_optimize_image_field(
            self, "favicon", max_size=self.FAVICON_MAX_SIZE, trim=True
        )
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "SiteBranding":
        """Return the singleton row, creating it lazily on first access.

        Reads the row directly on every call: a primary-key lookup on a
        single tiny text/image row is cheap enough that the simplicity
        beats juggling cross-process cache invalidation.
        """
        instance, _created = cls.objects.get_or_create(pk=1)
        return instance
