import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

CSS_DIR = Path(settings.BASE_DIR) / "static" / "css"
CSS = (CSS_DIR / "design-system.css").read_text(encoding="utf-8")
HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")


def _block(css, selector):
    """Text between `selector {` (at line start) and its matching `}`."""
    match = re.search(rf"(?m)^{re.escape(selector)}\s*\{{", css)
    if not match:
        raise AssertionError(f"selector not found: {selector}")
    start = match.end()
    depth = 1
    i = start
    while i < len(css) and depth:
        ch = css[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    if depth:
        raise AssertionError(f"unbalanced braces for: {selector}")
    return css[start : i - 1]


class PearlTokenTests(SimpleTestCase):
    def test_light_tokens(self):
        b = _block(CSS, '[data-bs-theme="light"]')
        for k, v in {
            "--bs-body-bg": "#fafaf8",
            "--rd-ink": "#0a0a0a",
            "--rd-accent": "#ff5a1f",
            "--bs-border-color": "#ececea",
            "--rd-primary-bg": "#0a0a0a",
        }.items():
            self.assertRegex(b, rf"{re.escape(k)}:\s*{v}\b", k)

    def test_dark_tokens(self):
        b = _block(CSS, '[data-bs-theme="dark"]')
        for k, v in {
            "--bs-body-bg": "#0b0b0a",
            "--rd-ink": "#f5f5f2",
            "--rd-accent": "#ff6a33",
            "--rd-surface": "#151514",
            "--rd-primary-bg": "#f5f5f2",
        }.items():
            self.assertRegex(b, rf"{re.escape(k)}:\s*{v}\b", k)

    def test_fonts(self):
        root = _block(CSS, ":root")
        self.assertIn('"IBM Plex Sans Arabic"', root)
        self.assertIn('"Noto Kufi Arabic"', root)
        self.assertIn('"Inter Tight"', root)

    def test_old_font_families_gone(self):
        self.assertNotIn('"Cairo"', CSS)
        self.assertNotRegex(CSS, r'"Inter"')

    def test_no_hex_outside_token_blocks(self):
        self.assertIn("/* @end-tokens */", CSS)
        body = CSS.split("/* @end-tokens */", 1)[1]
        offenders = [l for l in body.splitlines() if HEX.search(l)]
        self.assertEqual(
            offenders, [], "Use Pearl tokens, not raw hex, below the token blocks"
        )

    def test_end_tokens_marker_follows_dark_block(self):
        dark = _block(CSS, '[data-bs-theme="dark"]')
        dark_end = CSS.index(dark) + len(dark)
        marker = CSS.find("/* @end-tokens */")
        self.assertGreater(
            marker, dark_end, "/* @end-tokens */ must come after the dark token block"
        )

    def test_page_css_has_no_hex(self):
        for f in ("app.css", "number-check.css", "login.css", "employee.css"):
            text = (CSS_DIR / f).read_text(encoding="utf-8")
            self.assertEqual([l for l in text.splitlines() if HEX.search(l)], [], f)

    def test_employee_css_keeps_focus_outline(self):
        # employee.css loads after the "kept last" ink focus ring in design-system.css,
        # so an `outline: none` here would silently remove the keyboard focus indicator.
        text = (CSS_DIR / "employee.css").read_text(encoding="utf-8")
        offenders = [
            l.strip() for l in text.splitlines() if re.search(r"outline\s*:\s*(none|0)\b", l)
        ]
        self.assertEqual(offenders, [], "employee.css must not remove outlines")


# --- Colour helpers (WCAG 2.x relative luminance / contrast ratio) -----------
_HEX_COLOUR = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")


def _parse_colour(value):
    """`#rgb`, `#rrggbb` or `rgb(r, g, b)` -> (r, g, b) ints."""
    value = value.strip()
    m = _HEX_COLOUR.fullmatch(value)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))
    m = re.fullmatch(r"rgb\(\s*(\d+)\s*[, ]\s*(\d+)\s*[, ]\s*(\d+)\s*\)", value)
    if m:
        return tuple(int(g) for g in m.groups())
    raise AssertionError(f"unsupported colour: {value!r}")


def _luminance(rgb):
    def chan(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (chan(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b):
    la, lb = sorted((_luminance(_parse_colour(a)), _luminance(_parse_colour(b))), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _token(block, name):
    m = re.search(rf"{re.escape(name)}:\s*([^;]+);", block)
    if not m:
        raise AssertionError(f"{name} not defined")
    return m.group(1).strip()


def _rules(css):
    """Yield (selector-list, body) for every flat `selector { ... }` rule."""
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selectors = re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S)
        yield [s.strip() for s in selectors.split(",") if s.strip()], m.group(2)


def _rule_body(css, selector):
    """Body of the first rule whose selector list contains `selector` exactly."""
    for selectors, body in _rules(css):
        if selector in selectors:
            return body
    raise AssertionError(f"rule not found: {selector}")


class FieldBorderTokenTests(SimpleTestCase):
    THEMES = ('[data-bs-theme="light"]', '[data-bs-theme="dark"]')

    def test_field_border_token_defined_in_both_themes(self):
        for theme in self.THEMES:
            self.assertIn("--rd-field-border:", _block(CSS, theme), theme)

    def test_field_border_contrast(self):
        for theme in self.THEMES:
            block = _block(CSS, theme)
            border = _token(block, "--rd-field-border")
            surface = _token(block, "--rd-surface")
            ratio = _contrast(border, surface)
            self.assertGreaterEqual(ratio, 3.0, f"{theme}: {border} on {surface} = {ratio:.2f}")

    def test_fields_use_field_border(self):
        employee = (CSS_DIR / "employee.css").read_text(encoding="utf-8")
        login = (CSS_DIR / "login.css").read_text(encoding="utf-8")
        for css, selector in (
            (CSS, ".form-control"),
            (CSS, ".form-select"),
            (CSS, ".form-check-input"),
            (employee, ".rd-entry-field"),
            (employee, ".rd-pay-sub-notes__body .form-control"),
            (login, ".rd-login-input"),
        ):
            body = _rule_body(css, selector)
            self.assertRegex(
                body,
                r"border(-color)?:[^;]*var\(--rd-field-border\)",
                f"{selector} must use --rd-field-border",
            )

    def test_focus_ring_not_clipped_in_scroll_containers(self):
        wanted = {
            ".table-responsive .btn:focus-visible",
            ".rd-table-wrap .btn:focus-visible",
            ".rd-card.overflow-hidden .btn:focus-visible",
        }
        for selector in wanted:
            body = _rule_body(CSS, selector)
            self.assertRegex(body, r"outline-offset:\s*-2px", selector)

    def test_login_icon_uses_muted(self):
        login = (CSS_DIR / "login.css").read_text(encoding="utf-8")
        self.assertRegex(
            _rule_body(login, ".rd-login-icon"), r"color:\s*var\(--rd-muted\)"
        )

    def test_login_lang_focus_only_for_keyboard(self):
        login = (CSS_DIR / "login.css").read_text(encoding="utf-8")
        self.assertNotIn(".rd-login-lang:focus-within", login)
        self.assertIn(".rd-login-lang:has(select:focus-visible)", login)


class LoginScriptTests(SimpleTestCase):
    JS = (Path(settings.BASE_DIR) / "static" / "js" / "login.js").read_text(encoding="utf-8")

    def test_live_region_reset_uses_animation_frame(self):
        self.assertIn("requestAnimationFrame", self.JS)
        self.assertNotRegex(self.JS, r"setTimeout\([^;]*,\s*50\s*\)")

    def test_autofill_sync_hook(self):
        self.assertIn("animationstart", self.JS)


class FloatingPanelTokenTests(SimpleTestCase):
    THEMES = ('[data-bs-theme="light"]', '[data-bs-theme="dark"]')

    def test_raised_tokens_defined_in_both_themes(self):
        for theme in self.THEMES:
            block = _block(CSS, theme)
            self.assertIn("--rd-surface-raised:", block, theme)
            self.assertIn("--rd-shadow-pop:", block, theme)

    def test_dark_raised_surface_is_lighter_than_page_and_cards(self):
        block = _block(CSS, '[data-bs-theme="dark"]')
        raised = _token(block, "--rd-surface-raised")
        for other in ("--bs-body-bg", "--rd-surface"):
            ratio = _contrast(raised, _token(block, other))
            self.assertGreater(ratio, 1.1, f"raised vs {other}: {ratio:.2f}")

    def test_raised_surface_keeps_muted_text_readable(self):
        for theme in self.THEMES:
            block = _block(CSS, theme)
            ratio = _contrast(_token(block, "--rd-muted"), _token(block, "--rd-surface-raised"))
            self.assertGreaterEqual(ratio, 4.5, f"{theme}: {ratio:.2f}")


class PhoneLegendContrastTests(SimpleTestCase):
    """Owner ruling U3: the phone-status legend numbers take their swatch hue."""

    THEMES = ('[data-bs-theme="light"]', '[data-bs-theme="dark"]')
    ITEMS = ("active", "due", "grace", "overdue")

    def _resolve(self, value, *blocks):
        while value.startswith("var("):
            name = re.fullmatch(r"var\((--[\w-]+)\)", value).group(1)
            for block in blocks:
                m = re.search(rf"{re.escape(name)}:\s*([^;]+);", block)
                if m:
                    value = m.group(1).strip()
                    break
            else:
                raise AssertionError(f"{name} not defined")
        return value

    def _colour_decl(self, selector):
        body = _rule_body(CSS, selector)
        m = re.search(r"(?<![\w-])color:\s*([^;]+);", body)
        self.assertTrue(m, f"{selector} has no colour")
        return m.group(1).strip()

    def test_numbers_use_their_swatch_hue(self):
        donut = _rule_body(CSS, ".rd-phone-lifecycle")
        self.assertRegex(donut, r"--donut-active:\s*var\(--rd-success\)")
        self.assertRegex(donut, r"--donut-overdue:\s*var\(--rd-danger\)")
        # Ends today: orange family (accent for ring + swatch, accent-ink as text).
        self.assertRegex(donut, r"--donut-due:\s*var\(--rd-accent\)")
        self.assertEqual(
            self._colour_decl(".rd-phone-lifecycle__item--due .rd-phone-lifecycle__val"),
            "var(--rd-accent-ink)",
        )
        # Active / grace / overdue numbers use exactly the swatch variable.
        for item in ("active", "grace", "overdue"):
            self.assertEqual(
                self._colour_decl(f".rd-phone-lifecycle__item--{item} .rd-phone-lifecycle__val"),
                f"var(--donut-{item})",
                item,
            )

    def test_numbers_meet_text_contrast_in_both_themes(self):
        donut = _rule_body(CSS, ".rd-phone-lifecycle")
        for theme in self.THEMES:
            block = _block(CSS, theme)
            for surface_name in ("--rd-surface", "--rd-surface-2"):  # card + row hover
                surface = self._resolve(f"var({surface_name})", block)
                for item in self.ITEMS:
                    decl = self._colour_decl(f".rd-phone-lifecycle__item--{item} .rd-phone-lifecycle__val")
                    colour = self._resolve(decl, donut, block)
                    ratio = _contrast(colour, surface)
                    self.assertGreaterEqual(
                        ratio, 4.5, f"{theme} {item}: {decl} = {colour} on {surface_name} {surface} = {ratio:.2f}"
                    )

    def test_overdue_link_has_resting_affordance(self):
        body = _rule_body(CSS, "a.rd-phone-lifecycle__val")
        self.assertIn("text-decoration: underline dotted", body)
        self.assertIn("text-underline-offset: 0.2em", body)
        hover = _rule_body(CSS, "a.rd-phone-lifecycle__val:hover")
        self.assertIn("underline solid", hover)
        self.assertNotRegex(hover, r"(?<![\w-])color:")  # keeps the red


class DesignDocTokenTests(SimpleTestCase):
    def test_every_light_theme_token_is_documented(self):
        doc = (Path(settings.BASE_DIR) / "DESIGN.md").read_text(encoding="utf-8")
        block = _block(CSS, '[data-bs-theme="light"]')
        block = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
        names = sorted(set(re.findall(r"(?m)^\s*(--rd-[\w-]+)\s*:", block)))
        self.assertTrue(names)
        missing = [n for n in names if n not in doc]
        self.assertEqual(missing, [], "Add these --rd-* tokens to the DESIGN.md token table")


class LightEmptyStateAndSparkTests(SimpleTestCase):
    """Owner ruling U4: no grey wells, full-width sparkline."""

    def test_empty_state_has_no_fill_and_compact_padding(self):
        body = _rule_body(CSS, ".empty-state")
        self.assertRegex(body, r"background:\s*transparent")
        self.assertRegex(body, r"border:\s*1px dashed")
        m = re.search(r"padding:\s*([\d.]+)rem", body)
        self.assertTrue(m and float(m.group(1)) <= 1.25, body)

    def test_empty_state_inside_cards_has_no_box(self):
        text = "\n".join(
            body
            for sels, body in _rules(CSS)
            if any(s.startswith((".rd-card .empty-state", ".rd-card-body .empty-state")) for s in sels)
        )
        self.assertRegex(text, r"border:\s*0")
        self.assertRegex(text, r"background:\s*transparent")

    def test_empty_state_icon_and_title_are_soft(self):
        icon = _rule_body(CSS, ".empty-state__icon")
        self.assertRegex(icon, r"background:\s*var\(--rd-surface-2\)")
        self.assertRegex(icon, r"color:\s*var\(--rd-muted\)")
        self.assertNotIn("box-shadow", icon)
        w = float(re.search(r"width:\s*([\d.]+)rem", icon).group(1))
        self.assertLessEqual(w, 2.25)
        title = _rule_body(CSS, ".empty-state__title")
        self.assertIn("color: var(--rd-muted)", title)
        self.assertRegex(title, r"font-size:\s*0\.9rem")

    def test_mini_chart_empty_state_does_not_force_height(self):
        body = "\n".join(b for sels, b in _rules(CSS) if ".rd-mini-chart--empty .empty-state" in sels)
        self.assertNotRegex(body, r"min-height:\s*[1-9]")

    def test_sparkline_is_full_width_with_a_fixed_height(self):
        body = _rule_body(CSS, ".rd-spark")
        self.assertRegex(body, r"width:\s*100%")
        self.assertNotIn("max-width", body)
        self.assertRegex(body, r"height:\s*1\.75rem")
        self.assertRegex(body, r"flex:\s*0 0 100%")
        self.assertRegex(_rule_body(CSS, ".rd-stat-figure"), r"flex-wrap:\s*wrap")
