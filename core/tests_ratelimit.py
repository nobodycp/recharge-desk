"""Redis-backed fixed-window rate limiter."""
from __future__ import annotations

from unittest.mock import patch

from django.test import SimpleTestCase

from core.ratelimit import allow
from providers.redis_conn import get_redis


class AllowTests(SimpleTestCase):
    def setUp(self):
        get_redis().flushall()

    def test_allow_blocks_after_limit_in_window(self):
        self.assertTrue(allow("t", "1.2.3.4", 2, 60))
        self.assertTrue(allow("t", "1.2.3.4", 2, 60))
        self.assertFalse(allow("t", "1.2.3.4", 2, 60))
        # Other idents and other buckets have their own counters.
        self.assertTrue(allow("t", "5.6.7.8", 2, 60))
        self.assertTrue(allow("other", "1.2.3.4", 2, 60))

    def test_window_is_fixed_not_sliding(self):
        allow("t", "1.2.3.4", 5, 60)
        key = "ratelimit:t:1.2.3.4"
        get_redis().expire(key, 5)
        allow("t", "1.2.3.4", 5, 60)
        self.assertLessEqual(get_redis().ttl(key), 5)

    def test_window_starts_on_first_hit(self):
        allow("t", "1.2.3.4", 5, 60)
        ttl = get_redis().ttl("ratelimit:t:1.2.3.4")
        self.assertTrue(0 < ttl <= 60)

    def test_counter_without_ttl_is_repaired(self):
        get_redis().set("ratelimit:t:1.2.3.4", 1)
        self.assertTrue(allow("t", "1.2.3.4", 5, 60))
        self.assertGreater(get_redis().ttl("ratelimit:t:1.2.3.4"), 0)

    def test_zero_limit_disables(self):
        for _ in range(5):
            self.assertTrue(allow("t", "1.2.3.4", 0, 60))
        self.assertIsNone(get_redis().get("ratelimit:t:1.2.3.4"))

    def test_empty_ident_is_allowed(self):
        self.assertTrue(allow("t", "", 1, 60))
        self.assertTrue(allow("t", "", 1, 60))

    def test_redis_error_fails_open(self):
        with patch("core.ratelimit.get_redis", side_effect=ConnectionError("down")):
            with self.assertLogs("core.ratelimit", level="WARNING"):
                self.assertTrue(allow("t", "1.2.3.4", 1, 60))
