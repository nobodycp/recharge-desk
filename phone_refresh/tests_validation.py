from django.test import SimpleTestCase

from phone_refresh.validation import ALLOWED_PHONE_PREFIXES, is_valid_phone


class PhoneValidationTests(SimpleTestCase):
    def test_allowed_prefixes(self):
        for prefix in ALLOWED_PHONE_PREFIXES:
            self.assertTrue(is_valid_phone(f"{prefix}1234567"))

    def test_rejects_wrong_length(self):
        self.assertFalse(is_valid_phone("050123456"))
        self.assertFalse(is_valid_phone("05012345678"))

    def test_rejects_disallowed_prefixes(self):
        for bad in ("0561234567", "0571234567", "0591234567"):
            self.assertFalse(is_valid_phone(bad))

    def test_strips_whitespace(self):
        self.assertTrue(is_valid_phone("  0501234567  "))
