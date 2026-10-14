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
