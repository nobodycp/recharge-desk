"""Employee sale entry: Pearl one-screen markup (hooks, field groups, no legacy colours)."""
import re
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase
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


    def test_blk_end_is_a_div(self):
        html = self._html()
        self.assertIn('<div class="rd-entry-blk__end">', html)
        self.assertNotIn('<span class="rd-entry-blk__end">', html)

    def test_payer_hint_is_not_inside_the_label(self):
        html = self._html()
        ancestors = _ancestor_classes(html, ("rd-payer-prefill-hint",))
        self.assertIn("rd-entry-field__top", ancestors["rd-payer-prefill-hint"])
        self.assertRegex(
            html,
            r'<label class="rd-entry-field__label" for="id_payer_name">\s*Payer name\s*</label>',
        )

    def test_notes_popover_closes_on_escape_and_outside_click(self):
        html = self._html()
        self.assertIn(".rd-entry-notes[open]", html)
        self.assertIn('"Escape"', html)

    def test_notes_popover_focuses_the_textarea_on_open(self):
        html = self._html()
        self.assertIn('notesDetails.addEventListener("toggle"', html)
        self.assertIn('getElementById("id_notes")', html)


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


CSS_DIR = Path(settings.BASE_DIR) / "static" / "css"
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")


def _rules(css):
    """[(selector list, declaration text)] for every flat rule (media bodies included)."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return [
        ([s.strip() for s in sel.split(",")], body)
        for sel, body in _RULE.findall(css)
    ]


def _bodies(css, selector):
    """Declaration text of every rule that lists exactly `selector`."""
    return [body for sels, body in _rules(css) if selector in sels]


class EmployeeEntryCssTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.css = (CSS_DIR / "employee.css").read_text(encoding="utf-8")
        cls.app_css = (CSS_DIR / "app.css").read_text(encoding="utf-8")

    def _assert_decl(self, selector, *needles):
        bodies = _bodies(self.css, selector)
        self.assertTrue(bodies, f"no rule for {selector} in employee.css")
        joined = " ".join(" ".join(b.split()) for b in bodies)
        for needle in needles:
            self.assertIn(needle, joined, f"{selector}: {needle}")

    def test_entry_css_ellipsizes_tile_names(self):
        self._assert_decl(
            ".rd-entry-tiles .employee-chip-label",
            "text-overflow: ellipsis",
            "white-space: nowrap",
        )
        self._assert_decl(".rd-entry-tiles > *", "min-width: 0")

    def test_touch_targets(self):
        for selector in (
            ".rd-entry-tiles .employee-chip",
            ".rd-entry-chips .employee-chip",
            ".rd-entry-pill",
            ".rd-entry-field .employee-clipboard-paste-btn",
        ):
            with self.subTest(selector=selector):
                self._assert_decl(selector, "min-height: 3.45rem")

    def test_app_css_has_no_entry_rules(self):
        for gone in (
            ".employee-package-chip",
            "#rdCustomerPaySubModal",
            "#rdEmployeeRefreshModal",
            ".rd-esim-switch",
            ".rd-sim-options",
            ".rd-sale-preview",
            ".employee-charge-sale-btn",
            ".sticky-save",
            ".employee-company-tile",
            ".employee-payment-tile",
            ".employee-product-line-tile",
            ".employee-packages-panel",
        ):
            with self.subTest(selector=gone):
                self.assertNotIn(gone, self.app_css)

    def test_record_payment_page_keeps_its_styling(self):
        """Ruling 1: rules still matching a live selector moved to employee.css."""
        for selector in (
            "#rd-pay-sub-payment-tiles .rd-pay-sub-pm-tile",
            "#rd-pay-sub-payment-tiles .rd-pay-sub-pm-tile.is-active",
            ".employee-chip",
            ".employee-chip-icon",
            ".employee-chip-label",
            ".employee-notes-details",
            ".employee-notes-summary",
        ):
            with self.subTest(selector=selector):
                self.assertTrue(_bodies(self.css, selector), selector)

    def test_hidden_guards(self):
        for selector in (
            ".rd-entry-note[hidden]",
            ".rd-entry-est[hidden]",
            ".rd-entry-hint[hidden]",
        ):
            with self.subTest(selector=selector):
                self._assert_decl(selector, "display: none")
        self.assertIn(":has(> :not([hidden]))", self.css)

    def test_charge_button_uses_accent_tokens_only(self):
        bodies = " ".join(_bodies(self.css, ".employee-charge-sale-btn"))
        self.assertNotIn("--bs-btn-", bodies)

    def test_notes_popover_does_not_push_layout(self):
        self._assert_decl(".rd-entry-notes__body", "position: absolute")

    def test_price_row_is_a_grid_with_days_column(self):
        self._assert_decl(".rd-entry-price-row", "display: grid")
        self.assertTrue(_bodies(self.css, ".rd-entry-price-row.has-days"))

    def test_pay_tiles_dim_when_muted(self):
        self._assert_decl("#payment-tiles.is-muted", "opacity: 0.5")

    def test_paste_button_rules_are_scoped_to_the_entry_field(self):
        """The phone-refresh page has its own .employee-clipboard-paste-btn."""
        for sels, _ in _rules(self.css):
            for sel in sels:
                if "employee-clipboard-paste-btn" in sel:
                    self.assertTrue(
                        sel.startswith(".rd-entry-field "), f"unscoped: {sel}"
                    )

    def test_dock_is_sticky_above_the_fab(self):
        self._assert_decl(".rd-entry-dock", "position: sticky", "bottom: var(--rd-entry-dock-offset)")
        self._assert_decl(".rd-entry-dock::before", "background: var(--bs-body-bg)", "border-top: 1px solid")
        # The static .sticky-save inside no longer carries the sticky offset.
        inner = " ".join(
            _bodies(self.css, ".rd-employee-app.has-employee-tabbar .rd-entry-dock .sticky-save")
        )
        self.assertIn("position: static", inner)
        # Dock offset (6.2rem) clears the FAB ring (6.075rem).
        self.assertIn("--rd-entry-dock-offset: calc(6.2rem", self.css)

    def test_tiles_and_price_row_sit_above_the_pill_hit_boxes(self):
        self._assert_decl(".rd-entry-tiles", "z-index: 2")
        self._assert_decl(".rd-entry-price-row", "z-index: 2")
        for selector in (".rd-entry-pill", ".rd-entry-note-link"):
            joined = " ".join(_bodies(self.css, selector))
            self.assertNotRegex(joined, r"z-index:\s*[1-9]", selector)

    def test_pill_is_readable(self):
        joined = " ".join(" ".join(b.split()) for b in _bodies(self.css, ".rd-entry-pill"))
        size = float(re.search(r"font-size:\s*([\d.]+)rem", joined).group(1))
        self.assertGreaterEqual(size, 0.85)
        # visible chip = 3.45rem box minus 2 x the negative margin, at least 1.9rem
        margin = float(re.search(r"margin-block:\s*-([\d.]+)rem", joined).group(1))
        self.assertGreaterEqual(3.45 - 2 * margin, 1.9)

    def test_note_link_is_ink_not_accent_or_danger(self):
        joined = " ".join(_bodies(self.css, ".rd-entry-note-link"))
        self.assertIn("color: var(--rd-ink)", joined)
        self.assertNotIn("accent", joined)
        self.assertNotIn("danger", joined)

    def test_note_popover_spans_the_card(self):
        self._assert_decl(".rd-entry-blk__head", "position: relative")
        self._assert_decl(".rd-entry-notes__body", "inset-inline: calc(-0.45rem - 1px)")

    def test_pay_tiles_stay_on_one_row_at_320(self):
        self._assert_decl(
            ".rd-entry-tiles--pay", "minmax(3.4rem, 1fr)"
        )

    def test_product_rows_share_a_line_when_they_fit(self):
        self._assert_decl(".employee-product-section", "flex-wrap: wrap")
