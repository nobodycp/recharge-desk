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
        # Wipe the singleton row between tests so each case starts from
        # a known empty state. Also clear the legacy cache key, in case
        # any other code path still pokes at it.
        SiteBranding.objects.all().delete()
        cache.delete(SITE_BRANDING_CACHE_KEY)

    # ---- model + singleton -----------------------------------------------
    def test_save_pins_pk_to_one(self):
        b = SiteBranding(pk=42)
        b.save()
        self.assertEqual(b.pk, 1)
        self.assertEqual(SiteBranding.objects.count(), 1)

    def test_load_creates_row_lazily(self):
        self.assertEqual(SiteBranding.objects.count(), 0)
        first = SiteBranding.load()
        self.assertEqual(first.pk, 1)
        self.assertEqual(SiteBranding.objects.count(), 1)
        # Second call must not create a duplicate row.
        SiteBranding.load()
        self.assertEqual(SiteBranding.objects.count(), 1)

    def test_save_is_visible_on_next_load(self):
        """A fresh ``load()`` after ``save()`` must reflect the new value.

        This guards against the previous behaviour where a per-process
        ``LocMemCache`` could keep returning a stale ``site_name`` for
        up to five minutes after the operator updated it from the
        admin panel.
        """
        SiteBranding.load()
        b = SiteBranding.objects.get(pk=1)
        b.site_name = "Updated"
        b.save()
        self.assertEqual(SiteBranding.load().site_name, "Updated")

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

    def test_login_page_uses_branded_site_name_not_request_host(self):
        """Django's stock LoginView injects ``site_name`` from
        ``get_current_site(request)`` which falls back to the HTTP
        host (``"testserver"`` in tests, ``"127.0.0.1"`` in dev). Our
        ``BilingualLoginView`` must strip that key so the
        operator-configured branding name wins regardless of the host
        the request arrives on.

        We deliberately use the default test client host
        (``testserver``) because it is always in ``ALLOWED_HOSTS``
        during tests under every settings module, then assert the
        host string does NOT leak into the rendered HTML.
        """
        b = SiteBranding.load()
        b.site_name = "Acme Telecom"
        b.save()
        r = Client().get(reverse("accounts:login"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Acme Telecom")
        self.assertNotContains(r, "testserver")

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


class NavNotificationsContextProcessorTests(TestCase):
    """The topbar bell counts must surface AWAITING + cash-PENDING sales
    only for management users, never for employees or anonymous visitors."""

    @classmethod
    def setUpTestData(cls):
        from decimal import Decimal

        from companies.models import Company, Product, ProductLine
        from sales.models import PaymentMethod, Sale

        cls.boss = User.objects.create_user("notif_boss", password="x")
        UserProfile.objects.update_or_create(
            user=cls.boss,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        cls.worker = User.objects.create_user("notif_worker", password="x")
        UserProfile.objects.update_or_create(
            user=cls.worker,
            defaults={"role": UserProfile.Role.EMPLOYEE, "is_active_profile": True},
        )

        co = Company.objects.create(name="Acme")
        line = ProductLine.objects.create(company=co, name="019")
        prod = Product.objects.create(
            line=line,
            variant_label="100 GB",
            cost_price=Decimal("8"),
            default_sell_price=Decimal("10"),
        )
        pm = PaymentMethod.objects.create(name="Cash")

        def _mk(status, on_account=False):
            return Sale.objects.create(
                reference_number="0500000000",
                payer_name="x",
                company=co,
                product=prod,
                payment_method=pm,
                sell_price_actual=Decimal("10"),
                cost_price_snapshot=Decimal("8"),
                profit_snapshot=Decimal("2"),
                status=status,
                on_account=on_account,
                created_by=cls.boss,
            )

        # Two awaiting + one cash-pending = badge total of 3.
        _mk(Sale.Status.AWAITING, on_account=True)
        _mk(Sale.Status.AWAITING, on_account=True)
        _mk(Sale.Status.PENDING)
        # On-account PENDING is "approved debt" — must NOT show up in pending.
        _mk(Sale.Status.PENDING, on_account=True)
        _mk(Sale.Status.PAID)

    def test_anonymous_request_has_no_badge(self):
        r = Client().get(reverse("accounts:login"))
        self.assertIsNone(r.context["nav_notifications"])

    def test_employee_does_not_see_management_badge(self):
        c = Client()
        c.force_login(self.worker)
        r = c.get(reverse("sales:employee_entry"))
        self.assertIsNone(r.context["nav_notifications"])

    def test_management_sees_correct_counts(self):
        c = Client()
        c.force_login(self.boss)
        r = c.get(reverse("reports:dashboard"))
        n = r.context["nav_notifications"]
        self.assertIsNotNone(n)
        self.assertEqual(n["awaiting"], 2)
        self.assertEqual(n["pending"], 1)
        self.assertEqual(n["total"], 3)
        # And the badge HTML must render in the topbar.
        self.assertContains(r, "rd-notif-badge")

    def test_poll_endpoint_returns_same_counts_as_context_processor(self):
        """The live-polling JSON endpoint and the initial-render context
        processor must agree, otherwise the badge would jump on the
        first poll for no reason. Same setUpTestData fixture is reused
        so any drift between the two code paths is caught here."""
        import json

        c = Client()
        c.force_login(self.boss)
        r = c.get(reverse("core:nav_notifications_poll"))
        self.assertEqual(r.status_code, 200)
        body = json.loads(r.content)
        self.assertEqual(body["awaiting"], 2)
        self.assertEqual(body["pending"], 1)
        self.assertEqual(body["total"], 3)
        # Translated labels are bundled too so the JS fallback can use
        # them if it ever needs to.
        self.assertIn("labels", body)

    def test_poll_endpoint_blocked_for_employees(self):
        c = Client()
        c.force_login(self.worker)
        r = c.get(reverse("core:nav_notifications_poll"))
        # @management_required redirects employees to the forbidden page.
        self.assertEqual(r.status_code, 302)


class GlobalSearchTests(TestCase):
    """The topbar search must surface matches across sales, customers,
    and customer payments, gated to management users only."""

    @classmethod
    def setUpTestData(cls):
        from decimal import Decimal

        from companies.models import Company, Product, ProductLine
        from customers.models import Customer, CustomerPayment
        from sales.models import PaymentMethod, Sale

        cls.boss = User.objects.create_user("search_boss", password="x")
        UserProfile.objects.update_or_create(
            user=cls.boss,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        cls.worker = User.objects.create_user("search_worker", password="x")
        UserProfile.objects.update_or_create(
            user=cls.worker,
            defaults={"role": UserProfile.Role.EMPLOYEE, "is_active_profile": True},
        )

        co = Company.objects.create(name="Co")
        line = ProductLine.objects.create(company=co, name="L")
        prod = Product.objects.create(
            line=line,
            variant_label="P",
            cost_price=Decimal("5"),
            default_sell_price=Decimal("10"),
        )
        pm = PaymentMethod.objects.create(name="cash")
        cls.cust = Customer.objects.create(name="Hazem Salah", created_by=cls.boss)
        cls.sale = Sale.objects.create(
            company=co,
            product=prod,
            reference_number="0590999777",
            payer_name="Hazem Salah",
            payment_method=pm,
            sell_price_actual=Decimal("10"),
            cost_price_snapshot=Decimal("5"),
            profit_snapshot=Decimal("5"),
            status=Sale.Status.PAID,
            created_by=cls.boss,
        )
        cls.payment = CustomerPayment.objects.create(
            customer=cls.cust,
            amount=Decimal("100"),
            payment_method=pm,
            notes="bank transfer for sale 0590999777",
            created_by=cls.boss,
        )

    def test_employee_blocked_from_search(self):
        c = Client()
        c.force_login(self.worker)
        r = c.get(reverse("core:search"), {"q": "Hazem"})
        self.assertEqual(r.status_code, 302)

    def test_empty_query_renders_form_only(self):
        c = Client()
        c.force_login(self.boss)
        r = c.get(reverse("core:search"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["total"], 0)
        self.assertNotContains(r, "Hazem Salah")

    def test_query_groups_results_by_section(self):
        c = Client()
        c.force_login(self.boss)
        r = c.get(reverse("core:search"), {"q": "Hazem"})
        self.assertEqual(r.status_code, 200)
        # Sale + customer both contain "Hazem".
        self.assertEqual(len(r.context["sales"]), 1)
        self.assertEqual(len(r.context["customers"]), 1)
        # Payment surfaces too via its FK to the matching customer —
        # that's the whole point of the cross-model section listing.
        self.assertEqual(len(r.context["payments"]), 1)

    def test_numeric_query_matches_sale_reference_and_payment_notes(self):
        c = Client()
        c.force_login(self.boss)
        r = c.get(reverse("core:search"), {"q": "0590999777"})
        self.assertEqual(len(r.context["sales"]), 1)
        # The payment's notes contain the reference too.
        self.assertEqual(len(r.context["payments"]), 1)

    def test_topbar_renders_global_search_box(self):
        c = Client()
        c.force_login(self.boss)
        r = c.get(reverse("reports:dashboard"))
        self.assertContains(r, 'id="rd-global-search"')
        # The icon-toggle wrapper must also be present so the JS can
        # find a hook to expand the panel on click. If the markup gets
        # accidentally collapsed back to the always-visible input the
        # toggle button disappears and this regression catches it.
        self.assertContains(r, "data-rd-search-toggle")

    def test_suggest_endpoint_returns_grouped_json(self):
        import json

        c = Client()
        c.force_login(self.boss)
        r = c.get(reverse("core:search_suggest"), {"q": "Hazem"})
        self.assertEqual(r.status_code, 200)
        body = json.loads(r.content)
        keys = {g["key"] for g in body["groups"]}
        # Sale (matched on payer name), customer, and payment (via FK
        # customer.name) are all expected to appear in the dropdown.
        self.assertEqual(keys, {"sales", "customers", "payments"})
        self.assertTrue(body["more_url"])

    def test_suggest_short_query_returns_no_groups(self):
        import json

        c = Client()
        c.force_login(self.boss)
        r = c.get(reverse("core:search_suggest"), {"q": "H"})
        body = json.loads(r.content)
        # Single-character queries are rejected to avoid table scans
        # for every keystroke.
        self.assertEqual(body["groups"], [])

    def test_suggest_blocked_for_employees(self):
        c = Client()
        c.force_login(self.worker)
        r = c.get(reverse("core:search_suggest"), {"q": "Hazem"})
        self.assertEqual(r.status_code, 302)


class MediaServingTests(TestCase):
    """Production media route must serve branding images with image/* types."""

    def test_serve_media_sets_webp_content_type(self):
        from django.core.files.storage import default_storage

        from core.media_serving import serve_media

        default_storage.save("branding/test-icon.webp", SimpleUploadedFile("test-icon.webp", b"RIFF", "image/webp"))
        self.addCleanup(lambda: default_storage.delete("branding/test-icon.webp"))

        request = Client().get("/media/branding/test-icon.webp").wsgi_request
        response = serve_media(request, "branding/test-icon.webp", document_root=default_storage.location)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/webp")


class FaviconPartialTests(TestCase):
    """Login and management shells must always emit a usable favicon link."""

    def test_login_emits_static_fallback_when_no_branding_files(self):
        SiteBranding.objects.all().delete()
        r = Client().get(reverse("accounts:login"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'rel="icon"')
        self.assertContains(r, "/static/phone_refresh/img/favicon.png")

    def test_login_emits_branded_favicon_with_correct_mime(self):
        b = SiteBranding.load()
        b.favicon = SimpleUploadedFile("tab.png", _png_bytes(32, 32), "image/png")
        b.save()
        r = Client().get(reverse("accounts:login"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, b.favicon.url)
        self.assertContains(r, 'type="image/webp"')
        self.assertContains(r, 'rel="shortcut icon"')

    def test_login_falls_back_to_logo_when_favicon_missing_on_disk(self):
        b = SiteBranding.load()
        b.logo = SimpleUploadedFile("logo.png", _png_bytes(64, 64), "image/png")
        b.save()
        SiteBranding.objects.filter(pk=1).update(favicon="branding/missing.webp")
        b.refresh_from_db()
        r = Client().get(reverse("accounts:login"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, b.logo.url)
        self.assertContains(r, 'rel="icon"')

