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

    def test_sky_supports_refresh_number_check_and_charge(self):
        self.assertTrue(supports("sky", Capability.REFRESH))
        self.assertTrue(supports("sky", Capability.NUMBER_INFO))
        self.assertTrue(supports("sky", Capability.CHARGE))
        self.assertTrue(supports("sky", Capability.CHECK_CHARGE))
