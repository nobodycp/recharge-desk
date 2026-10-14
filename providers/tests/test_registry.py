from __future__ import annotations

from django.test import SimpleTestCase

from providers.contracts import Capability
from providers.registry import ADAPTERS, get_adapter, supports


class RegistryTests(SimpleTestCase):
    def test_get_adapter_known_keys(self):
        for key in ("sky", "layan", "aloha", "areen"):
            self.assertIn(key, ADAPTERS)
            self.assertEqual(get_adapter(key).key, key)

    def test_get_adapter_unknown_raises_keyerror(self):
        with self.assertRaises(KeyError):
            get_adapter("nope")

    def test_supports_refresh_true_charge_false_for_sky(self):
        self.assertTrue(supports("sky", Capability.REFRESH))
        self.assertFalse(supports("sky", Capability.CHARGE))
