from __future__ import annotations

from django.test import SimpleTestCase


class InfraTests(SimpleTestCase):
    def test_get_redis_without_url_returns_working_fake(self):
        from providers.redis_conn import get_redis

        get_redis().set("k", "v")
        self.assertEqual(get_redis().get("k"), b"v")
        self.assertIs(get_redis(), get_redis())

    def test_celery_eager_without_redis_url(self):
        from config.celery import app

        self.assertIs(app.conf.task_always_eager, True)
