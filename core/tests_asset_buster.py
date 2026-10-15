import os
import tempfile
from pathlib import Path

from django.test import RequestFactory, SimpleTestCase, override_settings

from core import context_processors
from core.context_processors import _compute_default_asset_buster, theme


class AssetBusterFingerprintTests(SimpleTestCase):
    def _tree(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name)

    def _touch(self, base, rel, mtime):
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("/* x */")
        os.utime(path, (mtime, mtime))
        return path

    def test_any_js_css_under_known_dirs_changes_the_fingerprint(self):
        base = self._tree()
        self._touch(base, "css/design-system.css", 1_000_000)
        with override_settings(STATICFILES_DIRS=[str(base)], STATIC_ROOT=None):
            before = _compute_default_asset_buster()
            self.assertEqual(before, "1000000")
            self._touch(base, "js/x.js", 2_000_000)  # not in the old hard-coded list
            self.assertEqual(_compute_default_asset_buster(), "2000000")
            self._touch(base, "js/nested/deep.js", 3_000_000)
            self.assertEqual(_compute_default_asset_buster(), "3000000")
            self._touch(base, "vendor/lib.css", 4_000_000)
            self.assertEqual(_compute_default_asset_buster(), "4000000")

    def test_ignores_other_extensions_and_dirs(self):
        base = self._tree()
        self._touch(base, "css/a.css", 1_000_000)
        self._touch(base, "img/logo.js", 9_000_000)  # outside css/js/vendor
        self._touch(base, "css/a.png", 9_000_000)  # not css/js
        with override_settings(STATICFILES_DIRS=[str(base)], STATIC_ROOT=None):
            self.assertEqual(_compute_default_asset_buster(), "1000000")

    def test_static_root_is_included(self):
        base, root = self._tree(), self._tree()
        self._touch(base, "css/a.css", 1_000_000)
        self._touch(root, "js/collected.js", 5_000_000)
        with override_settings(STATICFILES_DIRS=[str(base)], STATIC_ROOT=str(root)):
            self.assertEqual(_compute_default_asset_buster(), "5000000")

    def test_missing_dirs_give_empty_string(self):
        base = self._tree() / "does-not-exist"
        with override_settings(STATICFILES_DIRS=[str(base)], STATIC_ROOT=None):
            self.assertEqual(_compute_default_asset_buster(), "")
        with override_settings(STATICFILES_DIRS=[], STATIC_ROOT=None):
            self.assertEqual(_compute_default_asset_buster(), "")

    def test_explicit_setting_still_wins(self):
        request = RequestFactory().get("/")
        with override_settings(ASSET_CACHE_BUSTER="explicit-1"):
            self.assertEqual(theme(request)["static_cache_query"], "?v=explicit-1")
        with override_settings(ASSET_CACHE_BUSTER=""):
            expected = f"?v={context_processors._DEFAULT_BUSTER}" if context_processors._DEFAULT_BUSTER else ""
            self.assertEqual(theme(request)["static_cache_query"], expected)
