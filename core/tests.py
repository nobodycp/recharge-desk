"""Tests for core app pieces (security headers, context processor, branding)."""

import io

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import UserProfile
from core.models import SITE_BRANDING_CACHE_KEY, SiteBranding

User = get_user_model()


def _png_bytes(width: int = 16, height: int = 16) -> bytes:
    """Build a tiny in-memory PNG so tests don't need an asset fixture."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


class SecurityHeadersMiddlewareTests(TestCase):
    """SecurityHeadersMiddleware must add CSP + Permissions-Policy."""

    def setUp(self):
        self.client = Client()

    def test_csp_header_present_on_public_response(self):
        r = self.client.get(reverse("core:forbidden"))
        self.assertIn("Content-Security-Policy", r)
        csp = r["Content-Security-Policy"]
        self.assertIn("default-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertIn("object-src 'none'", csp)
        self.assertIn("form-action 'self'", csp)

    def test_csp_allows_inline_scripts_and_styles(self):
        """The codebase still ships inline script/style; CSP must permit them
        until they are migrated to nonces."""
        r = self.client.get(reverse("core:forbidden"))
        csp = r["Content-Security-Policy"]
        self.assertIn("script-src", csp)
        self.assertIn("'self'", csp)
        self.assertIn("'unsafe-inline'", csp)
        self.assertIn("style-src", csp)

    def test_csp_no_longer_whitelists_jsdelivr(self):
        """Bootstrap, HTMX and Alpine are self-hosted under /static/vendor/.
        If a future change re-introduces a CDN reference and silently
        re-adds jsdelivr to CSP we want CI to flag it loudly."""
        r = self.client.get(reverse("core:forbidden"))
        self.assertNotIn("cdn.jsdelivr.net", r["Content-Security-Policy"])

    def test_csp_allows_google_fonts(self):
        """The base templates still pull Cairo / Inter from Google Fonts.
        Removing this exception breaks typography on every page."""
        r = self.client.get(reverse("core:forbidden"))
        csp = r["Content-Security-Policy"]
        style_block = next(
            (p for p in csp.split(";") if p.strip().startswith("style-src")), ""
        )
        font_block = next(
            (p for p in csp.split(";") if p.strip().startswith("font-src")), ""
        )
        self.assertIn("https://fonts.googleapis.com", style_block)
        self.assertIn("https://fonts.gstatic.com", font_block)

    def test_csp_no_longer_grants_unsafe_eval(self):
        """Alpine.js was the only consumer of 'unsafe-eval'. After the
        nav-toggle.js rewrite the directive must stay out of CSP — if
        a future change re-introduces a library that needs it, this
        test should fail loudly so the trade-off is reconsidered."""
        r = self.client.get(reverse("core:forbidden"))
        self.assertNotIn("'unsafe-eval'", r["Content-Security-Policy"])

    def test_permissions_policy_disables_sensitive_apis(self):
        r = self.client.get(reverse("core:forbidden"))
        self.assertIn("Permissions-Policy", r)
        pp = r["Permissions-Policy"]
        for feature in ("geolocation", "camera", "microphone", "payment"):
            self.assertIn(f"{feature}=()", pp)

    def test_referrer_policy_set_via_django(self):
        """Django's SecurityMiddleware honors SECURE_REFERRER_POLICY."""
        r = self.client.get(reverse("core:forbidden"))
        self.assertEqual(r.get("Referrer-Policy"), "same-origin")

    def test_frame_options_deny(self):
        r = self.client.get(reverse("core:forbidden"))
        self.assertEqual(r.get("X-Frame-Options"), "DENY")

    def test_nosniff_header_present(self):
        r = self.client.get(reverse("core:forbidden"))
        self.assertEqual(r.get("X-Content-Type-Options"), "nosniff")

    @override_settings(SECURITY_HEADERS_ENABLED=False)
    def test_disabled_flag_skips_csp(self):
        """If a deployment has to disable CSP temporarily, the flag works."""
        # The middleware caches `enabled` at construction time; build a fresh
        # client so it picks up the override (the test client always
        # constructs a new wsgi handler, which re-instantiates middleware).
        r = Client().get(reverse("core:forbidden"))
        self.assertNotIn("Content-Security-Policy", r)


class SiteBrandingTests(TestCase):
    """Singleton SiteBranding model + login-page rendering + management editor."""

    @classmethod
    def setUpTestData(cls):
        cls.boss = User.objects.create_user("brand_boss", password="x")
        UserProfile.objects.update_or_create(
            user=cls.boss,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        cls.worker = User.objects.create_user("brand_worker", password="x")
        UserProfile.objects.update_or_create(
            user=cls.worker,
            defaults={"role": UserProfile.Role.EMPLOYEE, "is_active_profile": True},
        )

    def setUp(self):
        # Wipe both the singleton row and its cache between tests so each
        # case starts from a known empty state.
        SiteBranding.objects.all().delete()
        cache.delete(SITE_BRANDING_CACHE_KEY)

    # ---- model + singleton -----------------------------------------------
    def test_save_pins_pk_to_one(self):
        b = SiteBranding(pk=42)
        b.save()
        self.assertEqual(b.pk, 1)
        self.assertEqual(SiteBranding.objects.count(), 1)

    def test_load_creates_row_lazily_then_caches(self):
        self.assertEqual(SiteBranding.objects.count(), 0)
        first = SiteBranding.load()
        self.assertEqual(first.pk, 1)
        self.assertEqual(SiteBranding.objects.count(), 1)
        # Second call resolves from cache: no new row created.
        SiteBranding.load()
        self.assertEqual(SiteBranding.objects.count(), 1)
        self.assertIsNotNone(cache.get(SITE_BRANDING_CACHE_KEY))

    def test_save_invalidates_cache(self):
        SiteBranding.load()
        self.assertIsNotNone(cache.get(SITE_BRANDING_CACHE_KEY))
        b = SiteBranding.objects.get(pk=1)
        b.save()
        self.assertIsNone(cache.get(SITE_BRANDING_CACHE_KEY))

    # ---- login page integration -----------------------------------------
    def test_login_page_omits_logo_when_unset(self):
        r = Client().get(reverse("accounts:login"))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "login-brand-img")

    def test_login_page_renders_logo_when_set(self):
        b = SiteBranding.load()
        b.logo = SimpleUploadedFile("logo.png", _png_bytes(64, 64), "image/png")
        b.save()
        r = Client().get(reverse("accounts:login"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "login-brand-img")
        self.assertContains(r, b.logo.url)

    # ---- management editor ----------------------------------------------
    def test_employee_blocked_from_branding_editor(self):
        c = Client()
        c.force_login(self.worker)
        r = c.get(reverse("core:site_branding"))
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("core:forbidden"), r["Location"])

    def test_management_can_open_editor(self):
        c = Client()
        c.force_login(self.boss)
        r = c.get(reverse("core:site_branding"))
        self.assertEqual(r.status_code, 200)

    def test_upload_persists_and_runs_through_optimizer(self):
        c = Client()
        c.force_login(self.boss)
        upload = SimpleUploadedFile("brand.png", _png_bytes(800, 600), "image/png")
        r = c.post(reverse("core:site_branding"), {"logo": upload})
        self.assertEqual(r.status_code, 302)
        b = SiteBranding.load()
        self.assertTrue(b.logo)
        # The optimizer must have re-encoded the upload as a WebP.
        self.assertTrue(b.logo.name.endswith(".webp"))
