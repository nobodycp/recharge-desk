"""Employee sale entry: Pearl one-screen markup (hooks, field groups, no legacy colours)."""
import re
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase
from django.utils import timezone
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

    def test_progress_markup_and_script(self):
        html = self._html()
        self.assertIn('class="rd-entry-progress"', html)
        for step in ("pay", "co", "pk", "pr"):
            with self.subTest(step=step):
                self.assertIn(f'data-step="{step}"', html)
        self.assertIn('class="rd-entry-valid"', html)
        self.assertIn("js/employee-entry-progress.js", html)

    def test_progress_hairline_sits_in_the_top_bar_and_tick_in_the_number_field(self):
        html = self._html()
        header = html.split("</header>")[0]
        self.assertIn('class="rd-entry-progress"', header)
        self.assertNotIn('class="rd-entry-progress"', html.split("</header>")[1])
        ancestors = _ancestor_classes(html, ("id_reference_number",))
        self.assertIn("rd-entry-field", ancestors["id_reference_number"])
        field = html.split('for="id_reference_number"')[1].split("</button>")[0]
        self.assertIn('class="rd-entry-valid"', field)
        self.assertRegex(field, r'class="rd-entry-valid"[^>]*\bhidden\b')

    def test_step_numbers_sit_in_the_block_heads(self):
        html = self._html()
        self.assertEqual(len(re.findall(r'<div class="rd-entry-blk__head">\s*<span class="rd-entry-step"', html)), 4)

    def test_progress_script_inits_immediately_and_on_htmx_load(self):
        """A boosted visit re-inserts the script after htmx:load fired: init() must also run at top level."""
        js = (Path(settings.BASE_DIR) / "static" / "js" / "employee-entry-progress.js").read_text(encoding="utf-8")
        js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
        self.assertRegex(js, r"\n  init\(\);\n")
        self.assertIn("htmx.onLoad(init)", js)
        self.assertIn("form.__rdEntryProgress", js)

    def test_progress_script_is_display_only(self):
        js = (Path(settings.BASE_DIR) / "static" / "js" / "employee-entry-progress.js").read_text(encoding="utf-8")
        js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
        self.assertIn("htmx.onLoad", js)
        self.assertIn('"htmx:afterSwap"', js)
        self.assertIn("is-done", js)
        # Never writes a field value or touches submission.
        self.assertNotRegex(js, r"\.value\s*=[^=]")
        self.assertNotIn("preventDefault", js)
        self.assertNotIn("submit", js)
        # No document / window listeners (page-scope cleans them, but there is no need).
        self.assertNotRegex(js, r"(document|window)\.addEventListener\(\s*[\"'](input|change|click|htmx)")
        self.assertNotIn("window.__", js)


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

    def test_record_payment_pages_use_the_entry_tile_language(self):
        """Task 5: the payment tiles share the entry tile rules; the legacy
        borderless/transparent tile rules are gone."""
        for selector in (
            ".employee-chip",
            ".employee-chip-icon",
            ".employee-chip-label",
            ".employee-notes-details",
            ".employee-notes-summary",
        ):
            with self.subTest(selector=selector):
                self.assertTrue(_bodies(self.css, selector), selector)
        for sels, body in _rules(self.css):
            for sel in sels:
                if sel.startswith("#rd-pay-sub-payment-tiles"):
                    self.assertNotIn("border: none", body, sel)
                    self.assertNotIn("transparent !important", body, sel)
                    self.assertNotIn("--bs-primary", body, sel)

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

    def test_progress_hairline_takes_no_layout_space(self):
        self._assert_decl(
            ".rd-entry-progress", "position: absolute", "pointer-events: none"
        )
        self._assert_decl(".rd-entry-progress > i", "transform: scaleX(var(--rd-entry-progress, 0))")
        self.assertIn("var(--rd-accent)", " ".join(_bodies(self.css, ".rd-entry-progress > i")))
        self.assertNotRegex(
            " ".join(_bodies(self.css, ".rd-entry-progress > i")),
            r"transition:[^;]*\b(width|height|background)",
        )

    def test_step_dots_animate_transform_and_opacity_only(self):
        self._assert_decl(".rd-entry-step.is-done::before", "opacity: 1")
        for selector in (".rd-entry-step::before", ".rd-entry-step::after"):
            joined = " ".join(_bodies(self.css, selector))
            self.assertRegex(joined, r"transition:[^;]*(transform|opacity)", selector)
            self.assertNotRegex(joined, r"transition:[^;]*\b(width|height|background|color)\b", selector)

    def test_valid_tick_is_inside_the_field_grid_and_uses_success(self):
        self._assert_decl(
            ".rd-entry-valid", "grid-row: 1 / span 2", "align-self: center",
            "color: var(--rd-success)", "pointer-events: none"
        )
        self._assert_decl(".rd-entry-valid[hidden]", "display: none")
        joined = " ".join(_bodies(self.css, ".rd-entry-valid"))
        self.assertNotRegex(joined, r"\b(height|min-height):\s*[3-9]")

    def test_progress_track_is_visible_and_tick_pads_the_input(self):
        self.assertIn("var(--rd-faint)", " ".join(_bodies(self.css, ".rd-entry-progress")))
        self.assertIn(
            ".rd-entry-field:has(> .rd-entry-valid:not([hidden])) > .form-control", self.css
        )

    def test_progress_motion_respects_reduced_motion(self):
        block = self.css.split("prefers-reduced-motion: reduce", 1)[1]
        for needle in (".rd-entry-progress > i", ".rd-entry-valid", ".rd-entry-step::before"):
            with self.subTest(needle=needle):
                self.assertIn(needle, block)

    def test_entry_page_scroll_padding_clears_the_dock(self):
        self._assert_decl("html:has(.rd-entry-dock)", "scroll-padding-bottom")
        joined = " ".join(_bodies(self.css, "html:has(.rd-entry-dock)"))
        self.assertIn("--rd-entry-dock-offset", joined)
        self.assertIn("--rd-entry-dock-height", joined)


class EmployeePaymentPagesTests(TestCase):
    """Record payment + payment edit: Pearl markup, every JS hook kept."""

    @classmethod
    def setUpTestData(cls):
        from customers.models import Customer
        from customers.services import submit_customer_payment_submission
        from employees.models import EmployeeProfile

        cls.pm = PaymentMethod.objects.create(name="Cash Box")
        cls.pm2 = PaymentMethod.objects.create(name="Bank Wire")
        cls.emp = User.objects.create_user("emp_pay_pages", password="x")
        UserProfile.objects.update_or_create(
            user=cls.emp,
            defaults={"role": UserProfile.Role.EMPLOYEE, "is_active_profile": True},
        )
        EmployeeProfile.objects.create(user=cls.emp, monthly_salary=Decimal("0"))
        row = AppSettings.load()
        row.sales_show_record_payment = True
        row.sales_show_employee_payment = True
        row.require_settlement_request_approval = True
        row.save()
        cache.clear()
        cls.customer = Customer.objects.create(name="Pearl Cust", created_by=cls.emp)
        cls.sub = submit_customer_payment_submission(
            customer=cls.customer,
            amount=Decimal("12.00"),
            payment_method=cls.pm,
            notes="",
            user=cls.emp,
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.emp)

    def _record(self):
        resp = self.client.get(reverse("sales:employee_record_payment"))
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode()

    def _edit(self):
        resp = self.client.get(reverse("sales:employee_payment_edit", args=[self.sub.pk]))
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode()

    SHARED_HOOKS = [
        'id="rd-pay-sub-page"',
        "data-lookup-url=",
        "data-msg-customer=",
        "data-msg-payment=",
        'id="rd-pay-sub-form"',
        'id="rd-pay-sub-search"',
        'id="rd-pay-sub-ac"',
        'id="id_pay_sub_customer"',
        'id="rd-pay-sub-balance-line"',
        'id="rd-pay-sub-balance-val"',
        'id="id_pay_sub_amount"',
        'id="id_pay_sub_payment_method"',
        'id="id_pay_sub_paid_via_employee"',
        'id="rd-pay-sub-employee-toggle"',
        'id="rd-pay-sub-employee-hint"',
        'id="rd-pay-sub-payment-tiles"',
        "data-payment-id=",
        'id="id_pay_sub_notes"',
        'id="rd-pay-sub-submit"',
    ]
    LEGACY = ("btn-outline-info", "btn-info", "text-info", "text-primary", "row-cols-",
              "btn-outline-primary", "opacity-50", "badge bg-", "text-bg-")

    def test_record_page_keeps_every_hook(self):
        html = self._record()
        for hook in self.SHARED_HOOKS + [
            'id="rd-pay-sub-note-method"',
            'id="rd-pay-sub-note-employee"',
            'id="rd-pay-sub-notes-details"',
        ]:
            with self.subTest(hook=hook):
                self.assertIn(hook, html)

    def test_edit_page_keeps_the_back_link(self):
        html = self._edit()
        back = reverse("sales:employee_recent_sales")
        self.assertIn(
            '<a class="btn btn-outline-secondary btn-sm" href="%s">Back</a>' % back, html
        )
        self.assertIn('class="rd-entry-subrow', html)

    def test_positive_balance_hint_uses_the_kpi_success_token(self):
        from employees.models import EmployeeProfile

        EmployeeProfile.objects.filter(user=self.emp).update(current_balance=Decimal("40"))
        hint = self._record().split('-employee-hint"', 1)[1].split("Your balance:", 1)[0]
        self.assertIn("kpi-success", hint)
        self.assertNotIn("text-success", hint)

    def test_edit_page_keeps_every_hook(self):
        html = self._edit()
        for hook in self.SHARED_HOOKS:
            with self.subTest(hook=hook):
                self.assertIn(hook, html)

    def test_no_legacy_classes_on_either_page(self):
        for name, html in (("record", self._record()), ("edit", self._edit())):
            body = html.split('id="rd-pay-sub-page"', 1)[1]
            for legacy in self.LEGACY:
                with self.subTest(page=name, legacy=legacy):
                    self.assertNotIn(legacy, body)

    def test_pages_use_the_entry_building_blocks(self):
        for name, html in (("record", self._record()), ("edit", self._edit())):
            for needle in (
                "rd-entry-blk",
                "rd-entry-field",
                "rd-entry-tiles rd-entry-tiles--pay",
                "rd-entry-pill",
                "rd-entry-dock",
                "btn btn-primary",
            ):
                with self.subTest(page=name, needle=needle):
                    self.assertIn(needle, html)

    def test_tiles_are_buttons_with_names_and_no_btn_classes(self):
        for name, html in (("record", self._record()), ("edit", self._edit())):
            tiles = re.findall(r'<button[^>]*rd-pay-sub-pm-tile[^>]*>', html)
            self.assertEqual(len(tiles), 2, name)
            for tag in tiles:
                self.assertNotIn("btn-outline", tag)
                self.assertRegex(tag, r'class="employee-chip rd-pay-sub-pm-tile')
            for label in ("Cash Box", "Bank Wire"):
                self.assertRegex(
                    html, r'class="employee-chip-label"[^>]*>\s*%s\s*<' % label
                )

    def test_submit_is_the_ink_primary_button_in_the_dock(self):
        for name, html in (("record", self._record()), ("edit", self._edit())):
            submit = re.search(r'<button[^>]*id="rd-pay-sub-submit"[^>]*>', html).group(0)
            self.assertIn("btn-primary", submit, name)
            self.assertIn('type="submit"', submit, name)
            # outside the form box, tied to it with the form attribute
            self.assertIn('form="rd-pay-sub-form"', submit, name)
            dock = html.split("rd-entry-dock", 1)[1]
            self.assertIn('id="rd-pay-sub-submit"', dock, name)

    def test_edit_page_renders_the_employee_toggle_state_as_is_on(self):
        html = self._edit()
        tag = re.search(r'<button[^>]*id="rd-pay-sub-employee-toggle"[^>]*>', html).group(0)
        self.assertIn('aria-pressed="false"', tag)
        self.assertNotIn("is-on", tag)
        self.sub.paid_via_employee = True
        self.sub.payment_method = None
        self.sub.save()
        html = self._edit()
        tag = re.search(r'<button[^>]*id="rd-pay-sub-employee-toggle"[^>]*>', html).group(0)
        self.assertIn('aria-pressed="true"', tag)
        self.assertIn("is-on", tag)
        self.assertRegex(html, r'id="rd-pay-sub-payment-tiles"[^>]*>')
        self.assertIn("is-muted", html)

    def test_edit_page_marks_the_chosen_method_tile_active(self):
        html = self._edit()
        active = re.findall(r'<button[^>]*rd-pay-sub-pm-tile[^>]*is-active[^>]*>', html)
        self.assertEqual(len(active), 1)
        self.assertIn('data-payment-id="%d"' % self.pm.pk, active[0])

    def test_todays_payments_are_row_cards_with_pearl_badges(self):
        html = self._record()
        self.assertIn("Today’s payments", html)
        row = re.search(r'<div[^>]*id="payment-row-%d"[^>]*>' % self.sub.pk, html).group(0)
        self.assertIn("rd-mobile-row-card", row)
        self.assertIn("rd-badge rd-badge--pending", html)
        self.assertIn(reverse("sales:employee_payment_edit", args=[self.sub.pk]), html)
        self.assertIn("hx-target=\"#payment-row-%d\"" % self.sub.pk, html)

    def test_payment_pages_have_exactly_one_h1(self):
        for name, html in (("record", self._record()), ("edit", self._edit())):
            with self.subTest(page=name):
                self.assertEqual(html.count("<h1"), 1)
                self.assertIn('<h1 class="rd-employee-title">', html)

    def test_actions_wrapper_only_renders_when_an_action_exists(self):
        from types import SimpleNamespace

        from django.template.loader import render_to_string

        def render(**kw):
            p = SimpleNamespace(pk=7, resulting_payment_id=None, **kw)
            return render_to_string(
                "sales/partials/employee_payment_actions.html",
                {"p": p, "editable_payment_ids": {7}, "use_htmx": True},
            )

        self.assertEqual(render(status="approved").strip(), "")
        awaiting = render(status="awaiting")
        self.assertIn('class="rd-pay-actions"', awaiting)
        self.assertIn(reverse("sales:employee_payment_edit", args=[7]), awaiting)
        self.assertIn('hx-target="#payment-row-7"', awaiting)
        rejected = render(status="rejected")
        self.assertIn('class="rd-pay-actions"', rejected)
        self.assertNotIn(reverse("sales:employee_payment_edit", args=[7]), rejected)
        undo = render_to_string(
            "sales/partials/employee_payment_actions.html",
            {
                "p": SimpleNamespace(pk=7, status="approved", resulting_payment_id=3),
                "editable_payment_ids": {7},
            },
        )
        self.assertIn("rd-pay-actions", undo)
        self.assertIn("Undo", undo)

    def test_payment_actions_partial_has_no_legacy_button_colours(self):
        from pathlib import Path as _P

        text = (_P(settings.BASE_DIR) / "templates/sales/partials/employee_payment_actions.html").read_text()
        self.assertNotIn("btn-outline-primary", text)

    def test_script_uses_is_on_and_is_muted_not_bootstrap_colours(self):
        js = (Path(settings.BASE_DIR) / "static/js/employee-record-payment.js").read_text()
        self.assertIn('"is-on"', js)
        self.assertIn('"is-muted"', js)
        for legacy in ("btn-info", "btn-outline-info", "opacity-50"):
            self.assertNotIn(legacy, js)

    def test_script_treats_false_zero_and_empty_as_off(self):
        js = (Path(settings.BASE_DIR) / "static/js/employee-record-payment.js").read_text()
        body = js.split("function isPaySubViaEmployee()", 1)[1].split("}", 1)[0]
        self.assertIn(".toLowerCase()", body)
        for off in ('""', '"false"', '"0"'):
            self.assertIn(f"!== {off}", body)

    def test_hidden_field_renders_false_for_a_stored_false_and_the_pill_is_off(self):
        """The server value is "False"; the script maps it to off, the markup agrees."""
        html = self._edit()
        self.assertRegex(
            html, r'<input[^>]*type="hidden"[^>]*id="id_pay_sub_paid_via_employee"[^>]*value="False"'
            r'|<input[^>]*value="False"[^>]*id="id_pay_sub_paid_via_employee"'
        )
        tag = re.search(r'<button[^>]*id="rd-pay-sub-employee-toggle"[^>]*>', html).group(0)
        self.assertIn('aria-pressed="false"', tag)
        self.assertNotIn("is-on", tag)

    def test_script_inits_immediately_without_bind_once_guard(self):
        js = (Path(settings.BASE_DIR) / "static/js/employee-record-payment.js").read_text()
        # Runs on script execution (boosted visits re-insert the tag after htmx:load).
        self.assertTrue(js.rstrip().endswith("})();"))
        self.assertNotIn("window.__", js)
        self.assertNotIn("htmx.onLoad", js)

    def test_new_strings_are_translated(self):
        po = (Path(settings.BASE_DIR) / "locale/ar/LC_MESSAGES/django.po").read_text()
        for msgid in ("Current balance (owed to shop)", "Add note (optional)"):
            self.assertIn('msgid "%s"' % msgid, po)


class EmployeePaymentCssTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.css = (CSS_DIR / "employee.css").read_text(encoding="utf-8")

    def _assert_decl(self, selector, *needles):
        bodies = _bodies(self.css, selector)
        self.assertTrue(bodies, f"no rule for {selector} in employee.css")
        joined = " ".join(" ".join(b.split()) for b in bodies)
        for needle in needles:
            self.assertIn(needle, joined, f"{selector}: {needle}")

    def test_payment_tiles_share_the_entry_compact_grid(self):
        """<=6 methods on one row at 375 and 320: no page-specific grid or tile size."""
        self._assert_decl(".rd-entry-tiles--pay", "minmax(3.4rem, 1fr)")
        for sels, body in _rules(self.css):
            for sel in sels:
                if sel.startswith("#rd-pay-sub-payment-tiles") and "is-muted" not in sel:
                    self.assertNotIn("grid-template-columns", body, sel)
                    self.assertNotIn("min-height", body, sel)
                    self.assertNotIn("font-size", body, sel)
        joined = " ".join(_bodies(self.css, ".rd-entry-tiles .employee-chip"))
        self.assertIn("min-height: 3.45rem", " ".join(joined.split()))

    def test_balance_value_sits_next_to_its_label(self):
        joined = " ".join(" ".join(b.split()) for b in _bodies(self.css, ".rd-pay-sub-balance"))
        self.assertIn("justify-content: flex-start", joined)
        self.assertNotIn("space-between", joined)

    def test_payment_tiles_dim_when_muted(self):
        self._assert_decl("#rd-pay-sub-payment-tiles.is-muted", "opacity: 0.5")

    def test_dock_buttons_share_the_entry_dock_rule(self):
        self._assert_decl(".rd-entry-dock .rd-dock-btn", "min-height: 3.6rem")

    def test_record_page_pins_the_dock_to_the_bottom(self):
        self._assert_decl(".rd-pay-sub-dock", "margin-block-start: auto")
        self._assert_decl(".rd-employee-main:has(.rd-pay-sub)", "display: flex", "flex-direction: column")

    def test_autocomplete_rows_are_touch_sized(self):
        self._assert_decl(".rd-pay-sub-ac .list-group-item", "min-height: 3.45rem")

    def test_actions_and_links_are_touch_sized(self):
        self._assert_decl(".rd-pay-actions .btn", "min-height: 3.45rem")
        self._assert_decl(".rd-pay-sub-recent__all", "min-height: 3.45rem")
        self._assert_decl(".rd-pay-sub-notes .employee-notes-summary", "min-height: 3.45rem")

    def test_balance_row_has_a_hidden_guard(self):
        self._assert_decl(".rd-pay-sub-balance[hidden]", "display: none")


class EmployeeRecentEntriesTests(TestCase):
    """My entries: Pearl rows, filter grid and the shared sale badge partials."""

    @classmethod
    def setUpTestData(cls):
        from customers.models import Customer
        from customers.services import submit_customer_payment_submission
        from employees.models import EmployeeProfile
        from sales.services import create_sale

        cls.company = Company.objects.create(
            name="Recent Co", opening_balance=Decimal("1000"), current_balance=Decimal("1000")
        )
        line = ProductLine.objects.create(company=cls.company, name="Line")
        cls.product = Product.objects.create(
            line=line, variant_label="Pkg", cost_price=Decimal("5"), default_sell_price=Decimal("10")
        )
        cls.pm = PaymentMethod.objects.create(name="Cash Box")
        cls.emp = User.objects.create_user("emp_recent_ui", password="x")
        UserProfile.objects.update_or_create(
            user=cls.emp,
            defaults={"role": UserProfile.Role.EMPLOYEE, "is_active_profile": True},
        )
        EmployeeProfile.objects.create(user=cls.emp, monthly_salary=Decimal("0"))

        def sale(ref, price, **kw):
            return create_sale(
                company=cls.company, product=cls.product, reference_number=ref,
                payer_name="Payer " + ref, payment_method=cls.pm,
                sell_price_actual=Decimal(price), notes="", user=cls.emp, **kw
            )

        cls.plain = sale("0590000001", "10")
        cls.flagged = sale("0590000002", "10", is_esim=True, is_new_sim=True)
        cls.loss = sale("0590000003", "0")
        cls.customer = Customer.objects.create(name="Recent Cust", created_by=cls.emp)
        cls.sub = submit_customer_payment_submission(
            customer=cls.customer, amount=Decimal("12.00"), payment_method=cls.pm, notes="", user=cls.emp
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.emp)

    def _page(self):
        resp = self.client.get(reverse("sales:employee_recent_sales"))
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode()

    def _row(self, html, row_id):
        return re.search(r'<div[^>]*id="%s"[^>]*>' % row_id, html).group(0)

    def test_rows_keep_their_ids_and_become_row_cards(self):
        html = self._page()
        for sale in (self.plain, self.flagged, self.loss):
            tag = self._row(html, "sale-row-%d" % sale.pk)
            self.assertIn("rd-mobile-row-card", tag)
        self.assertIn("rd-mobile-row-card", self._row(html, "payment-row-%d" % self.sub.pk))
        self.assertNotIn("list-group-item", html.split('class="rd-recent"', 1)[1])

    def test_rows_use_pearl_badges_and_tabular_numbers(self):
        html = self._page()
        self.assertIn("rd-badge rd-badge--pending", html)
        self.assertRegex(html, r'class="rd-pay-row__amount tabular-nums"')
        self.assertRegex(html, r'<bdi class="tabular-nums">0590000001</bdi>')
        for legacy in ("badge bg-", "text-bg-", "btn-outline-primary", "table-light", "alert-"):
            self.assertNotIn(legacy, html.split('class="rd-recent"', 1)[1])

    def test_sale_chips_render_with_their_pearl_variants(self):
        html = self._page()
        self.assertRegex(html, r'class="rd-badge rd-badge--neutral rd-sale-esim-badge ms-1"[^>]*>eSIM<')
        self.assertRegex(html, r'class="rd-badge rd-badge--neutral rd-sale-new-sim-badge ms-1"')
        self.assertRegex(html, r'class="rd-badge rd-badge--cancelled rd-sale-loss-badge ms-1"[^>]*>Loss<')

    def test_edit_and_delete_keep_their_hx_attributes(self):
        html = self._page()
        self.assertRegex(
            html, r'class="btn btn-sm btn-outline-secondary" href="%s"'
            % reverse("sales:employee_sale_edit", args=[self.plain.pk])
        )
        self.assertIn('hx-target="#sale-row-%d"' % self.plain.pk, html)
        self.assertIn('hx-post="%s"' % reverse("sales:employee_sale_delete", args=[self.plain.pk]), html)
        self.assertIn("hx-confirm=", html)
        self.assertIn('hx-target="#payment-row-%d"' % self.sub.pk, html)
        self.assertIn('class="rd-pay-actions"', html)

    def test_filter_form_keeps_every_field_and_hook(self):
        html = self._page()
        for name in ("q", "company", "payment_method", "status", "date_from", "date_to"):
            with self.subTest(field=name):
                self.assertIn('id="id_%s"' % name, html)
                self.assertIn('name="%s"' % name, html)
        self.assertIn("data-rd-filter-count", html)
        self.assertIn('id="recent-htmx-indicator"', html)
        self.assertIn('class="rd-recent-filter"', html)
        self.assertNotIn("row g-3", html)
        self.assertRegex(html, r'<button class="btn btn-primary btn-sm" type="submit">')

    def test_scope_label_is_a_neutral_pearl_chip(self):
        html = self._page()
        self.assertRegex(html, r'<span class="rd-badge rd-badge--neutral"><bdi>[^<]+</bdi></span>\s*</header>')

    def test_auto_charge_row_keeps_its_charge_class(self):
        from sales.models import Sale

        Sale.objects.filter(pk=self.plain.pk).update(entry_mode="auto_charge", charge_status="success")
        html = self._page()
        tag = self._row(html, "sale-row-%d" % self.plain.pk)
        self.assertIn("rd-charge-row", tag)
        self.assertIn("rd-recent-row", tag)

    def test_empty_state_is_unchanged(self):
        from sales.models import Sale

        Sale.objects.all().delete()
        type(self.sub).objects.all().delete()
        self.assertIn("empty-state", self._page())


class SaleBadgePartialsTests(SimpleTestCase):
    """The badge partials are shared with management pages: they render the Pearl chip anywhere."""

    def _render(self, name, **sale):
        from types import SimpleNamespace

        from django.template.loader import render_to_string

        return render_to_string("partials/%s.html" % name, {"sale": SimpleNamespace(**sale)})

    def test_each_partial_renders_a_pearl_badge_with_text(self):
        cases = (
            ("sale_esim_badge", {"is_esim": True}, "rd-badge--neutral", "eSIM"),
            ("sale_new_sim_badge", {"is_new_sim": True}, "rd-badge--neutral", "New SIM"),
            ("sale_loss_badge", {"loss_snapshot": Decimal("3")}, "rd-badge--cancelled", "Loss"),
            (
                "sale_employee_payment_badge",
                {"paid_via_employee": True, "employee_recipient": None},
                "rd-badge--neutral",
                "Payment to employee",
            ),
        )
        for name, sale, variant, text in cases:
            with self.subTest(partial=name):
                out = self._render(name, **sale)
                self.assertIn("rd-badge " + variant, out)
                self.assertIn(">%s<" % text, out)
                self.assertNotIn("text-bg-", out)
                self.assertNotIn('class="badge', out)

    def test_each_partial_renders_nothing_when_it_does_not_apply(self):
        self.assertEqual(self._render("sale_esim_badge", is_esim=False).strip(), "")
        self.assertEqual(self._render("sale_new_sim_badge", is_new_sim=False).strip(), "")
        self.assertEqual(self._render("sale_loss_badge", loss_snapshot=Decimal("0")).strip(), "")
        self.assertEqual(
            self._render("sale_employee_payment_badge", paid_via_employee=False).strip(), ""
        )


class EmployeeRecentCssTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.css = (CSS_DIR / "employee.css").read_text(encoding="utf-8")

    def _assert_decl(self, selector, *needles):
        bodies = _bodies(self.css, selector)
        self.assertTrue(bodies, f"no rule for {selector} in employee.css")
        joined = " ".join(" ".join(b.split()) for b in bodies)
        for needle in needles:
            self.assertIn(needle, joined, f"{selector}: {needle}")

    def test_filter_grid_is_stacked_then_three_columns(self):
        self._assert_decl(".rd-recent-filter", "grid-template-columns: minmax(0, 1fr)")
        self.assertRegex(
            self.css,
            r"@media \(min-width: 768px\) \{\s*\.rd-recent-filter \{\s*grid-template-columns: repeat\(3, minmax\(0, 1fr\)\)",
        )

    def test_controls_and_buttons_are_touch_sized(self):
        for sels, body in _rules(self.css):
            if ".rd-recent-filter .form-control" in sels:
                self.assertIn("min-height: 3.45rem", " ".join(body.split()))
                self.assertIn(".rd-recent-filter .form-select", sels)
                break
        else:
            self.fail("no .rd-recent-filter .form-control rule")
        self._assert_decl(".rd-recent-filter__actions .btn", "min-height: 3.45rem")
        self._assert_decl(".rd-recent .rd-filter-card > summary", "min-height: 3.45rem")

    def test_charge_edge_uses_logical_properties_and_a_token(self):
        self._assert_decl(".rd-recent-row.rd-charge-row", "border-inline-start: 3px solid var(--rd-accent-2)")

    def test_recent_section_has_no_raw_colour_or_physical_sides(self):
        section = self.css.split("Recent entries (\"My entries\")", 1)[1]
        self.assertNotRegex(section, r"#[0-9a-fA-F]{3,8}\b")
        self.assertNotRegex(section, r"\b(margin|padding|border)-(left|right)\b|\b(left|right):")


class EmployeeSecondaryPagesTests(TestCase):
    """Sale edit, stock check and my balance on the Pearl building blocks."""

    @classmethod
    def setUpTestData(cls):
        from customers.models import Customer
        from employees.models import EmployeeLedgerEntry, EmployeeProfile
        from inventory.services import allocate_to_customer, receive_main_stock
        from sales.services import create_sale

        cls.company = Company.objects.create(
            name="Second Co", opening_balance=Decimal("1000"), current_balance=Decimal("1000")
        )
        cls.line = ProductLine.objects.create(company=cls.company, name="HotLine")
        cls.product = Product.objects.create(
            line=cls.line, variant_label="Pkg", cost_price=Decimal("5"), default_sell_price=Decimal("10")
        )
        cls.pm = PaymentMethod.objects.create(name="Cash Box")
        cls.mgmt = User.objects.create_user("mgmt_secondary_ui", password="x")
        UserProfile.objects.update_or_create(
            user=cls.mgmt, defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True}
        )
        cls.emp = User.objects.create_user("emp_secondary_ui", password="x")
        UserProfile.objects.update_or_create(
            user=cls.emp,
            defaults={"role": UserProfile.Role.EMPLOYEE, "is_active_profile": True},
        )
        cls.profile = EmployeeProfile.objects.create(
            user=cls.emp, monthly_salary=Decimal("2500"), current_balance=Decimal("1200")
        )
        cls.sale = create_sale(
            company=cls.company, product=cls.product, reference_number="0590000009",
            payer_name="Edit Payer", payment_method=cls.pm,
            sell_price_actual=Decimal("10"), notes="", user=cls.emp,
        )
        cls.entry = EmployeeLedgerEntry.objects.create(
            employee=cls.profile,
            entry_type=EmployeeLedgerEntry.EntryType.SALARY_ACCRUAL,
            amount=Decimal("2500"),
            salary_month=timezone.localdate().replace(day=1),
            notes="ledger note",
            created_by=cls.emp,
        )
        receive_main_stock(product_line=cls.line, qty=10, notes="", user=cls.mgmt)
        cls.customer = Customer.objects.create(name="Dealer Pearl", created_by=cls.mgmt)
        allocate_to_customer(
            customer=cls.customer, product_line=cls.line, qty=3, notes="", user=cls.mgmt
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.emp)

    def _get(self, name, *args):
        resp = self.client.get(reverse(name, args=args))
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode()

    def _edit(self):
        return self._get("sales:employee_sale_edit", self.sale.pk)

    # -- sale edit ---------------------------------------------------------
    def test_sale_edit_uses_entry_blocks_and_field_groups(self):
        html = self._edit()
        self.assertIn("rd-entry-blk", html)
        self.assertNotIn("row g-3", html)
        self.assertNotIn("col-md-6", html)
        for name in ("payer_name", "reference_number", "sell_price_actual", "notes", "payment_method"):
            m = re.search(r'<(?:input|textarea|select)[^>]*name="%s"' % name, html)
            self.assertIsNotNone(m, name)
            before = html[: m.start()]
            self.assertGreater(before.rfind('class="rd-field-group'), before.rfind("</form>"), name)
            self.assertGreater(before.rfind('class="rd-entry-field'), before.rfind('class="rd-field-group'), name)

    def test_sale_edit_keeps_the_back_link_in_the_subtitle_row(self):
        html = self._edit()
        back = reverse("sales:employee_recent_sales")
        self.assertRegex(
            html,
            r'(?s)<div class="rd-entry-subrow">\s*<p class="rd-entry-subtitle">.*?</p>\s*'
            r'<a class="btn btn-outline-secondary btn-sm" href="%s">Back</a>' % re.escape(back),
        )

    def test_sale_edit_labels_sit_inside_their_fields(self):
        html = self._edit()
        for fid in ("id_payer_name", "id_reference_number", "id_sell_price_actual", "id_notes", "id_payment_method"):
            self.assertRegex(html, r'<label class="rd-entry-field__label" for="%s"' % fid)

    def test_sale_edit_has_one_h1_and_a_non_heading_subtitle(self):
        html = self._edit()
        self.assertEqual(html.count("<h1"), 1)
        self.assertIn('class="rd-employee-title"', html)
        self.assertIn("rd-entry-subtitle", html)
        self.assertIn("Second Co · HotLine", html)
        self.assertIn("0590000009", html.split("rd-entry-subtitle", 1)[1][:400])

    def test_sale_edit_keeps_the_info_note_and_puts_actions_in_the_dock(self):
        html = self._edit()
        self.assertIn('class="alert alert-info', html)
        self.assertIn('role="status"', html)
        self.assertIn("rd-entry-dock", html)
        dock = html.split('class="rd-entry-dock', 1)[1]
        self.assertRegex(dock, r'<button[^>]*class="btn btn-primary[^"]*"[^>]*type="submit"|<button[^>]*type="submit"[^>]*class="btn btn-primary')
        self.assertIn("Save changes", dock)
        self.assertRegex(
            dock, r'<a class="btn btn-outline-secondary[^"]*" href="%s"' % reverse("sales:employee_recent_sales")
        )
        self.assertIn("Cancel", dock)
        self.assertIn('id="employee-sale-edit-form"', html)
        self.assertIn('form="employee-sale-edit-form"', dock)

    def test_sale_edit_shows_a_pearl_status_badge_and_no_legacy_classes(self):
        html = self._edit()
        self.assertIn("rd-badge rd-badge--pending", html)
        self.assertRegex(html, r'<span class="tabular-nums">\d\d:\d\d</span>')
        for legacy in ("badge bg-", "text-bg-", "table-light", "btn-outline-primary", "form-label"):
            self.assertNotIn(legacy, html)

    def test_sale_edit_post_error_keeps_field_errors_under_the_field(self):
        resp = self.client.post(
            reverse("sales:employee_sale_edit", args=[self.sale.pk]),
            {"payment_method": self.pm.pk, "payer_name": "x", "reference_number": "123",
             "sell_price_actual": "10", "notes": ""},
        )
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn("rd-field-group has-error", html)
        self.assertEqual(html.count("<h1"), 1)

    # -- stock check -------------------------------------------------------
    def test_stock_check_search_is_an_entry_field_with_its_htmx_hooks(self):
        html = self._get("sales:employee_stock_check")
        self.assertEqual(html.count("<h1"), 1)
        self.assertIn("rd-entry-field", html)
        self.assertRegex(html, r'<label class="rd-entry-field__label" for="id_inv_customer_q"')
        tag = re.search(r'<input[^>]*name="q"[^>]*>', html).group(0)
        for needle in (
            'type="search"', 'id="id_inv_customer_q"', "autofocus", 'hx-target="#rd-stock-check-results"',
            'hx-select="#rd-stock-check-results"', 'hx-swap="outerHTML"', 'hx-push-url="true"',
            'hx-indicator="#rd-stock-check-indicator"', 'hx-trigger="keyup changed delay:180ms, search, clear"',
            'hx-get="%s"' % reverse("sales:employee_stock_check"),
        ):
            self.assertIn(needle, tag)
        self.assertNotIn("form-control-sm", tag)
        self.assertIn('id="rd-stock-check-indicator"', html)
        self.assertRegex(html, r'class="[^"]*rd-entry-field__action[^"]*"[^>]*\n?[^>]*href="%s"' % reverse("sales:employee_stock_check"))

    def test_stock_check_reset_keeps_its_hx_and_onclick(self):
        html = self._get("sales:employee_stock_check")
        tag = re.search(r'<a\b[^>]*rd-entry-field__action[^>]*>', html, re.S).group(0)
        self.assertIn('hx-get="%s"' % reverse("sales:employee_stock_check"), tag)
        self.assertIn('hx-target="#rd-stock-check-results"', tag)
        self.assertIn("onclick=", tag)
        self.assertNotIn("btn-sm", tag)

    def test_stock_check_results_are_compact_pearl_cards(self):
        html = self._get("sales:employee_stock_check")
        self.assertIn('id="rd-stock-check-results"', html)
        self.assertIn("rd-stock-check-card", html)
        self.assertIn("Dealer Pearl", html)
        self.assertRegex(html, r'class="rd-stock-check-card__total tabular-nums"[^>]*>3<')
        self.assertRegex(html, r'rd-stock-check-lines__qty tabular-nums')

    # -- my balance --------------------------------------------------------
    def test_my_balance_has_a_hero_balance_and_a_plain_salary_stat(self):
        html = self._get("employees:my_balance")
        self.assertEqual(html.count("<h1"), 1)
        self.assertIn("rd-stat rd-stat--hero", html)
        hero = html.split("rd-stat--hero", 1)[1].split("rd-stat-salary", 1)[0]
        self.assertIn("Current balance", hero)
        self.assertRegex(hero, r'rd-stat-value tabular-nums[^>]*>\s*<span dir="ltr">1,200')
        self.assertIn("rd-stat-salary", html)
        salary = html.split("rd-stat-salary", 1)[1].split("Movement statement", 1)[0]
        self.assertIn("Monthly salary", salary)
        self.assertRegex(salary, r'tabular-nums[^>]*>\s*2,500')
        self.assertNotIn("rd-stat--hero", salary)
        self.assertEqual(html.count("rd-stat--hero"), 1)

    def test_my_balance_keeps_its_hooks_and_headings(self):
        html = self._get("employees:my_balance")
        self.assertIn("rd-employee-wallet", html)
        self.assertIn("Wallet details", html)
        self.assertRegex(html, r'<h2 class="rd-heading-lg[^"]*"[^>]*>\s*Movement statement')
        for legacy in ("badge bg-", "text-bg-", "table-light", "text-success", "h3 mb-2"):
            self.assertNotIn(legacy, html.split("Wallet details", 1)[1].split("rd-mobile-tabbar", 1)[0])

    def test_ledger_rows_keep_their_hooks_and_get_card_classes(self):
        html = self._get("employees:my_balance")
        self.assertIn('id="employee-ledger-row-%d"' % self.entry.pk, html)
        self.assertIn("rd-ledger-card", html)
        self.assertRegex(html, r'rd-ledger-card__amount tabular-nums')
        self.assertIn("rd-badge rd-badge--neutral", html)
        self.assertIn("ledger note", html)
        self.assertNotIn(reverse("employees:employee_ledger_delete", args=[self.profile.pk, self.entry.pk]), html)

    def test_management_ledger_still_renders_the_shared_partial(self):
        self.client.force_login(self.mgmt)
        resp = self.client.get(reverse("employees:employee_detail", args=[self.profile.pk]) + "?tab=ledger")
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('id="employee-ledger-row-%d"' % self.entry.pk, html)
        self.assertIn("rd-mobile-row-card", html)
        self.assertIn(reverse("employees:employee_ledger_delete", args=[self.profile.pk, self.entry.pk]), html)
        self.assertIn("rd-datagrid-sort", html)

    # -- fix round 1 -------------------------------------------------------
    def _on_account_pending(self):
        from sales.models import Sale

        Sale.objects.filter(pk=self.sale.pk).update(
            on_account=True, status=Sale.Status.PENDING, payment_method=None
        )

    def test_sale_edit_status_text_is_get_status_display_for_on_account_pending(self):
        from django.utils import translation

        self._on_account_pending()
        html = self._edit()
        self.assertIn('rd-badge rd-badge--pending">Pending<', html)
        self.assertNotIn("NotPaid", html)
        self.addCleanup(translation.deactivate)
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "ar"
        resp = self.client.get(
            reverse("sales:employee_sale_edit", args=[self.sale.pk]), follow=True
        )
        self.assertEqual(resp.status_code, 200, resp.redirect_chain)
        html = resp.content.decode()
        self.assertIn('rd-badge rd-badge--pending">معلّق<', html)
        self.assertNotIn("غير مدفوع", html)

    def test_sale_edit_status_badge_variants_keep_their_text(self):
        from sales.models import Sale

        for status, variant in (("paid", "paid"), ("awaiting", "neutral")):
            Sale.objects.filter(pk=self.sale.pk).update(status=status)
            label = Sale.Status(status).label
            self.assertIn('rd-badge rd-badge--%s">%s<' % (variant, label), self._edit())

    def test_sale_edit_dock_is_the_shared_sticky_dock_outside_the_form(self):
        html = self._edit()
        form = html.split('id="employee-sale-edit-form"', 1)[1].split("</form>", 1)[0]
        self.assertNotIn("rd-entry-dock", form)
        self.assertEqual(html.count('class="rd-entry-dock '), 1)
        self.assertRegex(html, r'class="rd-entry-dock rd-pay-sub-dock">\s*<div class="sticky-save">')

    def test_my_balance_hero_keeps_the_sign_tone(self):
        from employees.models import EmployeeProfile

        html = self._get("employees:my_balance")
        self.assertRegex(html, r'rd-stat-value tabular-nums is-pos"><span dir="ltr">1,200')
        EmployeeProfile.objects.filter(pk=self.profile.pk).update(current_balance=Decimal("-35"))
        html = self._get("employees:my_balance")
        self.assertRegex(html, r'rd-stat-value tabular-nums is-neg"><span dir="ltr">-35')
        EmployeeProfile.objects.filter(pk=self.profile.pk).update(current_balance=Decimal("0"))
        html = self._get("employees:my_balance")
        self.assertNotRegex(html, r'rd-stat-value tabular-nums is-(pos|neg)')

    def test_wallet_amount_is_isolated_left_to_right(self):
        from employees.models import EmployeeProfile

        EmployeeProfile.objects.filter(pk=self.profile.pk).update(current_balance=Decimal("-35"))
        html = self._get("employees:my_balance")
        self.assertRegex(html, r'<span class="rd-employee-wallet__amt tabular-nums" dir="ltr">-35')

    def test_ledger_pager_is_a_pearl_pager_with_unchanged_links(self):
        from employees.models import EmployeeLedgerEntry

        for i in range(30):
            EmployeeLedgerEntry.objects.create(
                employee=self.profile,
                entry_type=EmployeeLedgerEntry.EntryType.ADJUSTMENT,
                amount=Decimal("1"),
                created_by=self.emp,
            )
        html = self._get("employees:my_balance")
        nav = html.split('class="rd-datagrid-pagination"', 1)[1].split("</nav>", 1)[0]
        self.assertNotIn("style=", nav)
        self.assertIn("rd-datagrid-pagination__bar", nav)
        self.assertRegex(nav, r'<li class="page-item rd-pager__num active">\s*<a class="page-link" href="[^"]*" aria-current="page">1</a>')
        self.assertIn("rd-pager__step", nav)
        self.assertRegex(nav, r'class="page-item rd-pager__step">\s*<a class="page-link" href="\?ledger_page=2">Next')
        self.assertRegex(nav, r'class="page-item rd-pager__edge">\s*<a class="page-link" href="\?ledger_page=2" title="Last" aria-label="Last">')
        self.assertRegex(nav, r'class="rd-datagrid-per-page__link is-active" aria-current="true" href="\?per_page=25&amp;ledger_page=1"')
        self.assertRegex(nav, r'class="rd-datagrid-per-page__link " href="\?per_page=10&amp;ledger_page=1"')

    # -- fix round 2: signed amounts read left to right in RTL ---------------
    def _negative(self):
        from employees.models import EmployeeLedgerEntry, EmployeeProfile

        EmployeeProfile.objects.filter(pk=self.profile.pk).update(current_balance=Decimal("-35"))
        return EmployeeLedgerEntry.objects.create(
            employee=self.profile,
            entry_type=EmployeeLedgerEntry.EntryType.ADJUSTMENT,
            amount=Decimal("-50"),
            created_by=self.emp,
        )

    def test_my_balance_hero_and_ledger_amounts_are_ltr_isolated(self):
        self._negative()
        html = self._get("employees:my_balance")
        self.assertRegex(html, r'rd-stat-value tabular-nums is-neg"><span dir="ltr">-35')
        # table cell (desktop branch) and mobile card (shared partial)
        self.assertRegex(html, r'<td class="text-end tabular-nums[^"]*kpi-expense">\s*<span dir="ltr">-50')
        self.assertRegex(html, r'<span dir="ltr" class="rd-ledger-card__amount tabular-nums[^"]*kpi-expense">-50')
        # the positive row is isolated the same way
        self.assertRegex(html, r'<span dir="ltr">2,500')

    def test_management_ledger_amounts_keep_their_cell_alignment(self):
        self._negative()
        self.client.force_login(self.mgmt)
        resp = self.client.get(reverse("employees:employee_detail", args=[self.profile.pk]) + "?tab=ledger")
        html = resp.content.decode()
        self.assertRegex(html, r'<td class="text-end tabular-nums[^"]*kpi-expense">\s*<span dir="ltr">-50')

    def test_balance_lines_on_entry_and_payment_pages_are_isolated(self):
        self._negative()
        row = AppSettings.load()
        row.sales_show_employee_payment = True
        row.sales_show_record_payment = True
        row.save()
        cache.clear()
        for name in ("sales:employee_entry", "sales:employee_record_payment"):
            html = self._get(name)
            self.assertRegex(html, r'Your balance: <span dir="ltr">-35', name)
        record = self._get("sales:employee_record_payment")
        self.assertIn('class="rd-pay-sub-balance__v tabular-nums" dir="ltr"', record)


class EmployeeSecondaryCssTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.css = (CSS_DIR / "employee.css").read_text(encoding="utf-8")

    def _assert_decl(self, selector, *needles):
        bodies = _bodies(self.css, selector)
        self.assertTrue(bodies, f"no rule for {selector} in employee.css")
        joined = " ".join(" ".join(b.split()) for b in bodies)
        for needle in needles:
            self.assertIn(needle, joined, f"{selector}: {needle}")

    def _section(self):
        return self.css.split("Sale edit, stock check, my balance", 1)[1]

    def test_back_link_is_a_touch_target_scoped_to_the_employee_app(self):
        self._assert_decl(".rd-employee-app .rd-entry-subrow .btn", "min-height: 3.45rem")

    def test_main_clears_the_fab_ring_on_pages_without_the_dock(self):
        bodies = _bodies(self.css, ".rd-employee-app.has-employee-tabbar .rd-employee-main")
        self.assertEqual(len(bodies), 2, "base and >=768px variants")
        for body in bodies:
            self.assertIn("padding-bottom: calc(6.2rem + env(safe-area-inset-bottom))", body)

    def test_chip_label_is_one_rule_and_the_dock_owns_the_press_scale(self):
        self.assertEqual(len(_bodies(self.css, ".employee-chip-label")), 1)
        self.assertNotIn("employee-chip-icon-fallback", self.css)
        self._assert_decl(".rd-entry-dock .employee-save-sale-btn:active", "transform: scale(0.99)")

    def test_design_system_has_no_dead_employee_form_rules(self):
        ds = (CSS_DIR / "design-system.css").read_text(encoding="utf-8")
        for gone in (
            ".rd-list-flush > .list-group-item.rd-charge-row",
            ".sticky-save .employee-save-sale-btn",
            ".employee-save-sale-btn:active",
            ".has-error .input-group .form-control",
        ):
            self.assertNotIn(gone, ds)
        self.assertEqual(_bodies(ds, ".rd-sale-form"), [])

    def test_sale_edit_shares_the_payment_page_column_and_dock(self):
        for sels, _body in _rules(self.css):
            if ".rd-employee-main:has(.rd-pay-sub)" in sels:
                self.assertIn(".rd-employee-main:has(.rd-sale-edit)", sels)
                break
        else:
            self.fail("no page column rule")

    def test_select_and_textarea_sit_in_the_entry_field(self):
        self._assert_decl(".rd-sale-form .rd-entry-field .form-select", "grid-row: 2")
        self._assert_decl(".rd-sale-form .rd-entry-field--area", "grid-template-rows: auto")

    def test_stock_check_reset_is_a_touch_sized_field_segment(self):
        self._assert_decl(".rd-entry-field .rd-entry-field__action", "min-height: 3.45rem", "grid-row: 1 / span 2")
        self._assert_decl(".rd-entry-field .rd-entry-field__action:focus-visible", "outline-offset: -3px")

    def test_stock_check_overrides_are_scoped_to_the_employee_card(self):
        for sel in (
            ".rd-stock-check-card .rd-stock-check-lines__row",
            ".rd-stock-check-card .rd-stock-check-lines__qty",
        ):
            self._assert_decl(sel)
        for sels, _body in _rules(self._section()):
            for s in sels:
                if "rd-stock-check-lines" in s:
                    self.assertIn(".rd-stock-check-card", s)

    def test_ledger_overrides_are_scoped_to_the_employee_shell(self):
        for sels, _body in _rules(self._section()):
            for s in sels:
                if "rd-ledger-card" in s or "rd-table-dual" in s:
                    self.assertTrue(s.startswith(".rd-employee-app "), s)
        self._assert_decl(".rd-employee-app .rd-ledger-card__amount", "font-weight: 700")

    def test_balance_hero_and_salary_stat(self):
        self._assert_decl(".rd-balance-stats", "display: flex")
        self._assert_decl(".rd-stat-salary .rd-stat-value", "font-size")

    def test_section_has_no_raw_colour_or_physical_sides(self):
        section = self._section()
        self.assertNotRegex(section, r"#[0-9a-fA-F]{3,8}\b")
        self.assertNotRegex(section, r"\b(margin|padding|border)-(left|right)\b|\b(left|right):")
        self.assertNotIn("outline: none", section)

    def test_flat_blocks_and_hero_tones_use_existing_tokens(self):
        self._assert_decl(".rd-sale-edit .rd-entry-blk", "border: 0", "background: none", "padding: 0")
        self._assert_decl(".rd-stock-check .rd-entry-blk", "border: 0")
        self._assert_decl(".rd-balance-stats .rd-stat--hero .rd-stat-value.is-neg", "var(--rd-danger-on-ink)")
        self._assert_decl(".rd-balance-stats .rd-stat--hero .rd-stat-value.is-pos", "var(--rd-success-on-ink)")
        # The field border is the shared field-border token; the edit page adds no override.
        self._assert_decl(".rd-entry-field", "var(--rd-field-border)")
        for sels, _body in _rules(self._section()):
            for s in sels:
                self.assertFalse(s.endswith(".rd-entry-field") and "rd-sale-edit" in s, s)

    def test_pager_targets_are_touch_sized_and_scoped(self):
        self._assert_decl(".rd-employee-app .pagination .page-item .page-link", "min-height: 3.45rem", "min-width: 3.45rem")
        self._assert_decl(".rd-employee-app .rd-datagrid-per-page__link", "min-height: 3.45rem", "min-width: 3.45rem")
        # The numbered window is never hidden: it wraps instead.
        self._assert_decl(".rd-employee-app .rd-datagrid-pagination__pages", "flex-wrap: wrap", "gap: 0.3rem")
        self._assert_decl(".rd-employee-app .rd-datagrid-pagination__pages .rd-pager__num", "flex: 0 0 3.45rem")
        for sels, body in _rules(self._section()):
            if any("rd-pager__num" in s or "rd-pager__gap" in s for s in sels):
                self.assertNotIn("display: none", " ".join(body.split()), sels)
        self._assert_decl(".rd-employee-app .rd-datagrid-per-page__link.is-active", "text-decoration: underline")
        for sels, _body in _rules(self._section()):
            for s in sels:
                if "pagination" in s or "per-page" in s or "pager" in s:
                    self.assertTrue(s.startswith(".rd-employee-app "), s)
