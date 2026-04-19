"""Tests for core app pieces (security headers, context processor)."""

from django.test import Client, TestCase, override_settings
from django.urls import reverse


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
