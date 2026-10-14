import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

CSS = (Path(settings.BASE_DIR) / "static" / "css" / "design-system.css").read_text(
    encoding="utf-8"
)


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
