"""Site branding editor on the page kit: header, savebar, dark logo preview."""

import io
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from accounts.models import UserProfile
from core.models import SiteBranding


def _png():
    buf = io.BytesIO()
    Image.new("RGBA", (4, 1), (0, 0, 0, 255)).save(buf, format="PNG")
    return buf.getvalue()


class SiteBrandingPageTests(TestCase):
    def setUp(self):
        cache.clear()  # SiteBranding.load() is cached across tests
        self.addCleanup(cache.clear)
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=media)
        override.enable()
        self.addCleanup(override.disable)
        user = get_user_model().objects.create_user("brand_boss", password="pw-12345678")
        UserProfile.objects.update_or_create(
            user=user, defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True}
        )
        self.client.force_login(user)

    def _save_logo(self):
        branding = SiteBranding.load()
        branding.logo = SimpleUploadedFile("mark.png", _png(), content_type="image/png")
        branding.save()
        return SiteBranding.objects.get(pk=1)

    def test_page_uses_settings_width_header_and_calm_savebar(self):
        response = self.client.get(reverse("core:site_branding"))
        self.assertContains(response, "rd-page rd-page--settings")
        self.assertContains(response, "rd-page-head")
        self.assertContains(response, "No unsaved changes")
        self.assertContains(response, "data-rd-savebar-save disabled")
        self.assertContains(response, 'data-rd-bind="site-name"')
        self.assertContains(response, 'data-rd-bind="tagline"')

    def test_savebar_sits_outside_the_preview_grid(self):
        html = self.client.get(reverse("core:site_branding")).content.decode()
        self.assertLess(html.index("rd-brand-layout"), html.index("data-rd-savebar"))
        self.assertGreater(html.index("data-rd-savebar"), html.index("</aside>"))

    def test_saved_logo_gets_a_dark_preview_image(self):
        branding = self._save_logo()
        html = self.client.get(reverse("core:site_branding")).content.decode()
        dark = branding.logo_dark_url
        self.assertEqual(html.count(f'data-rd-dark-src="{dark}"'), 2)  # sidebar + login preview
        self.assertEqual(html.count("rd-bp-logo--light"), 2)
        self.assertIn(f'data-rd-light-src="{branding.logo.url}"', html)

    def test_no_saved_logo_has_no_dark_preview(self):
        html = self.client.get(reverse("core:site_branding")).content.decode()
        self.assertNotIn("data-rd-dark-src", html)

    def test_remove_current_image_checkbox_still_posts_clear(self):
        self._save_logo()
        response = self.client.get(reverse("core:site_branding"))
        self.assertContains(response, 'name="logo-clear"')
        self.assertContains(response, "data-rd-drop-clear")

    def test_field_error_is_inline_and_savebar_starts_dirty(self):
        response = self.client.post(reverse("core:site_branding"), {"site_name": "x" * 130})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "rd-field__error")
        self.assertContains(response, 'aria-invalid="true"')
        self.assertContains(response, "data-rd-savebar-force")
