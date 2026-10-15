"""Site logo in dark mode: transparent like light mode, black ink turned light."""

import io
import shutil
import tempfile
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from accounts.models import UserProfile
from core.image_utils import dark_logo_variant
from core.models import SiteBranding


def _png(pixels, size=(4, 1)):
    img = Image.new("RGBA", size)
    img.putdata(pixels)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class DarkLogoVariantTests(TestCase):
    def _convert(self, pixels):
        img = Image.new("RGBA", (len(pixels), 1))
        img.putdata(pixels)
        return list(dark_logo_variant(img).getdata())

    def test_black_ink_becomes_light(self):
        (r, g, b, a), = self._convert([(10, 10, 10, 255)])
        self.assertGreaterEqual(min(r, g, b), 150)
        self.assertEqual(a, 255)

    def test_brand_colours_are_kept(self):
        red, gold, dark_red = (200, 10, 20, 255), (210, 170, 40, 255), (90, 0, 0, 255)
        self.assertEqual(self._convert([red, gold, dark_red]), [red, gold, dark_red])

    def test_highlights_stay_brighter_than_body(self):
        body, highlight = self._convert([(20, 20, 20, 255), (200, 200, 200, 255)])
        self.assertGreater(highlight[0], body[0])

    def test_transparency_is_kept(self):
        self.assertEqual(self._convert([(0, 0, 0, 0)])[0][3], 0)
        self.assertEqual(self._convert([(5, 5, 5, 120)])[0][3], 120)


class SiteBrandingDarkLogoTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)

    def _branding_with_logo(self):
        branding = SiteBranding.load()
        branding.logo = SimpleUploadedFile(
            "mark.png",
            _png([(0, 0, 0, 255), (200, 0, 0, 255), (0, 0, 0, 0), (0, 0, 0, 255)]),
            content_type="image/png",
        )
        branding.save()
        return SiteBranding.objects.get(pk=1)

    def test_no_logo_no_dark_url(self):
        self.assertIsNone(SiteBranding.load().logo_dark_url)

    def test_dark_variant_written_next_to_logo(self):
        branding = self._branding_with_logo()
        url = branding.logo_dark_url
        self.assertTrue(url.endswith(".dark.webp"), url)
        name = url[len(settings.MEDIA_URL):]
        self.assertTrue((Path(self.media) / name).is_file())

    def test_dark_variant_rebuilt_when_missing(self):
        branding = self._branding_with_logo()
        name = branding.logo_dark_url[len(settings.MEDIA_URL):]
        (Path(self.media) / name).unlink()
        branding = SiteBranding.objects.get(pk=1)  # the URL is memoised per instance
        self.assertEqual(branding.logo_dark_url[len(settings.MEDIA_URL):], name)
        self.assertTrue((Path(self.media) / name).is_file())

    def test_dark_url_is_computed_once_per_instance_and_reset_on_save(self):
        from unittest import mock

        branding = self._branding_with_logo()
        with mock.patch("core.models.ensure_dark_logo", return_value="/media/x.dark.webp") as ensure:
            for _ in range(3):
                self.assertEqual(branding.logo_dark_url, "/media/x.dark.webp")
            self.assertEqual(ensure.call_count, 1)
            branding.save()  # save() itself builds the copy once, then the memo is empty again
            calls = ensure.call_count
            self.assertEqual(branding.logo_dark_url, "/media/x.dark.webp")
            self.assertEqual(ensure.call_count, calls + 1)

    def test_sidebar_renders_light_and_dark_logos(self):
        branding = self._branding_with_logo()
        user = get_user_model().objects.create_user("boss", password="pw-12345678")
        UserProfile.objects.update_or_create(
            user=user,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        self.client.force_login(user)
        resp = self.client.get(reverse("reports:dashboard"))
        self.assertContains(resp, 'class="rd-sidebar-brand-logo rd-sidebar-brand-logo--light"')
        self.assertContains(
            resp,
            f'class="rd-sidebar-brand-logo rd-sidebar-brand-logo--dark" src="{branding.logo_dark_url}"',
        )


class SidebarLogoCssTests(TestCase):
    def setUp(self):
        self.css = (Path(settings.BASE_DIR) / "static/css/design-system.css").read_text()

    def test_no_light_plate_behind_dark_logo(self):
        self.assertNotIn('[data-bs-theme="dark"] .rd-sidebar-brand-logo {', self.css)

    def test_each_theme_shows_its_own_logo(self):
        self.assertIn(
            '[data-bs-theme="dark"] .rd-sidebar-brand-logo--light,\n'
            ':root:not([data-bs-theme="dark"]) .rd-sidebar-brand-logo--dark {\n'
            "  display: none;\n}",
            self.css,
        )
