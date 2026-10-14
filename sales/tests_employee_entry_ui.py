"""Employee sale entry: Pearl one-screen markup (hooks, field groups, no legacy colours)."""
import re
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import UserProfile
from companies.models import Company, Product, ProductLine
from core.models import AppSettings
from sales.models import PaymentMethod

User = get_user_model()

_VOID = {"input", "br", "img", "hr", "meta", "link", "source", "wbr"}


def _ancestor_classes(html, ids):
    """Map each element id to the set of CSS classes on its ancestors."""
    from html.parser import HTMLParser

    found = {}

    class P(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if a.get("id") in ids:
                found[a["id"]] = {c for cls in self.stack for c in cls}
            if tag not in _VOID:
                self.stack.append((a.get("class") or "").split())

        def handle_startendtag(self, tag, attrs):
            a = dict(attrs)
            if a.get("id") in ids:
                found[a["id"]] = {c for cls in self.stack for c in cls}

        def handle_endtag(self, tag):
            if tag not in _VOID and self.stack:
                self.stack.pop()

    P().feed(html)
    return found


class EmployeeEntryMarkupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(
            name="Alpha Telecom",
            opening_balance=Decimal("1000"),
            current_balance=Decimal("1000"),
        )
        cls.company2 = Company.objects.create(
            name="Beta Mobile",
            opening_balance=Decimal("1000"),
            current_balance=Decimal("1000"),
        )
        for company, line_name in ((cls.company, "AlphaLine"), (cls.company2, "BetaLine")):
            line = ProductLine.objects.create(company=company, name=line_name)
            Product.objects.create(
                line=line,
                variant_label="Monthly",
                cost_price=Decimal("5"),
                default_sell_price=Decimal("10"),
            )
        cls.pm = PaymentMethod.objects.create(name="Cash Box")
        cls.pm2 = PaymentMethod.objects.create(name="Bank Wire")
        cls.emp = User.objects.create_user("emp_markup", password="x")
        UserProfile.objects.update_or_create(
            user=cls.emp,
            defaults={"role": UserProfile.Role.EMPLOYEE, "is_active_profile": True},
        )
        settings_row = AppSettings.load()
        settings_row.sales_inventory_enabled = True
        settings_row.save()
        cache.clear()

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.emp)

    def _html(self):
        resp = self.client.get(
            reverse("sales:employee_entry"), {"company": str(self.company.pk)}
        )
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode()

    def test_all_js_hooks_present(self):
        html = self._html()
        hooks = [
            'id="employee-sale-form"',
            "data-esim-extra=",
            "data-sim-preview-url=",
            "data-msg-phone-prefix=",
            'name="form_token"',
            'id="id_reference_number"',
            'id="rd-ref-error"',
            'id="rd-payer-assist-root"',
            "data-api-payer-by-number=",
            "data-api-payer-name-suggestions=",
            'id="rd-payer-prefill-hint"',
            'id="rd-payer-ac-list"',
            'class="rd-payer-ac-anchor"',
            'id="rd-payment-section"',
            'id="rd-on-account-toggle"',
            'id="payment-tiles"',
            "data-payment-id=",
            'id="id_payment_method"',
            'id="id_on_account"',
            'id="id_paid_via_employee"',
            'id="product-tiles-loading"',
            'id="company-tiles"',
            'hx-target="#product-tiles-container"',
            'hx-indicator="#product-tiles-loading"',
            'id="id_company"',
            'id="id_product"',
            'id="product-tiles-container"',
            'id="rd-duration-days-wrap"',
            'id="rd-sell-price-wrap"',
            'id="rd-days-hint"',
            'id="id_is_esim"',
            'id="id_is_new_sim"',
            'id="rd-new-sim-extra-label"',
            'id="rd-sim-serial-wrap"',
            'id="rd-sim-stock-preview"',
            'id="rd-sale-preview"',
            'id="rd-preview-cost"',
            'id="rd-preview-profit"',
            'id="rd-preview-esim-note"',
            'id="rd-preview-new-sim-note"',
            'id="rd-preview-loss-note"',
            'id="id_notes"',
            'value="save"',
            'class="employee-clipboard-paste-btn',
        ]
        for hook in hooks:
            with self.subTest(hook=hook):
                self.assertIn(hook, html)
        self.assertEqual(html.count("data-paste-target="), 2)

    def test_package_chips_keep_js_hooks_and_show_price(self):
        html = self._html()
        for hook in (
            'id="employee-line-tiles"',
            'id="employee-packages-panel"',
            "employee-product-line-tile",
            "employee-package-chip",
            "data-product-id=",
            "data-product-cost=",
            "data-default-price=",
            "data-base-days=",
            "data-line-id=",
        ):
            with self.subTest(hook=hook):
                self.assertIn(hook, html)
        self.assertRegex(
            html, r'employee-package-chip[^>]*>\s*<b[^>]*>Monthly</b>\s*<span[^>]*>10(\.00)?</span>'
        )

    def test_each_field_sits_in_a_field_group(self):
        """The submit handler resolves the error group via closest('.rd-field-group')."""
        html = self._html()
        ancestors = _ancestor_classes(html, ("id_reference_number", "id_payer_name"))
        for field_id in ("id_reference_number", "id_payer_name"):
            with self.subTest(field=field_id):
                self.assertIn("rd-field-group", ancestors[field_id])
        self.assertIn('saleRefInput.closest(".rd-field-group")', html)

    def test_no_legacy_colour_classes(self):
        html = self._html()
        for legacy in (
            "btn-outline-info",
            "btn-outline-warning",
            "btn-outline-primary",
            "text-info",
            "text-primary",
            "row-cols-",
        ):
            with self.subTest(legacy=legacy):
                self.assertNotIn(legacy, html)

    def test_entry_js_uses_is_on_not_bootstrap_colours(self):
        html = self._html()
        self.assertTrue(
            'classList.toggle("is-on"' in html or 'classList.add("is-on"' in html
        )
        for legacy in (
            '"btn-info"',
            '"btn-warning"',
            '"btn-outline-info"',
            '"btn-outline-warning"',
            '"col-sm-9"',
        ):
            with self.subTest(legacy=legacy):
                self.assertNotIn(legacy, html)
        self.assertIn("has-days", html)
        self.assertIn("is-muted", html)
        self.assertIn("rd-entry-note--danger", html)

    def test_tiles_show_names(self):
        html = self._html()
        for name in ("Cash Box", "Bank Wire", "Alpha Telecom", "Beta Mobile"):
            with self.subTest(name=name):
                self.assertRegex(
                    html,
                    r'class="employee-chip-label"[^>]*>\s*%s\s*<' % re.escape(name),
                )


class EmployeeIconMarkTests(TestCase):
    def _render(self, **ctx):
        from django.template.loader import render_to_string

        return render_to_string("sales/partials/employee_icon_mark.html", ctx)

    def test_show_name_adds_label_next_to_icon(self):
        class Icon:
            url = "/media/x.png"

        html = self._render(icon=Icon(), title="Cash Box", show_name=True)
        self.assertIn('class="employee-chip-icon"', html)
        self.assertIn('alt=""', html)
        self.assertRegex(html, r'class="employee-chip-label">Cash Box<')

    def test_default_keeps_icon_only_with_alt(self):
        class Icon:
            url = "/media/x.png"

        html = self._render(icon=Icon(), title="Cash Box")
        self.assertIn('alt="Cash Box"', html)
        self.assertNotIn("employee-chip-label", html)
