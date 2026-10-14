"""Deployment wiring: Celery routing vs. the worker's queues, production env guards."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from config.celery import app as celery_app

BASE_DIR = Path(settings.BASE_DIR)
PROJECT_PREFIXES = ("providers.", "phone_refresh.", "sales.", "sms_gateway.")


def _routed_queue(task_name: str) -> str:
    route = celery_app.amqp.router.route({}, task_name)
    return route["queue"].name


def _project_task_names() -> set:
    celery_app.loader.import_default_modules()
    return {
        name
        for name in celery_app.tasks
        if name.startswith(PROJECT_PREFIXES)
    }


class CeleryRoutingTests(SimpleTestCase):
    def test_worker_queue_list_matches_entrypoint(self):
        text = (BASE_DIR / "entrypoint.sh").read_text(encoding="utf-8")
        match = re.search(r"celery -A config worker[^\n]*?-Q\s+(\S+)", text.replace("\\\n", " "))
        self.assertIsNotNone(match, "entrypoint.sh worker branch must pass -Q <queues>")
        self.assertEqual(
            tuple(match.group(1).split(",")), tuple(settings.CELERY_WORKER_QUEUES)
        )

    def test_every_beat_task_routes_to_a_worker_queue(self):
        queues = set(settings.CELERY_WORKER_QUEUES)
        self.assertTrue(settings.CELERY_BEAT_SCHEDULE)
        for entry_name, entry in settings.CELERY_BEAT_SCHEDULE.items():
            task = entry["task"]
            with self.subTest(beat=entry_name, task=task):
                self.assertIn(_routed_queue(task), queues)

    def test_every_project_shared_task_routes_to_a_worker_queue(self):
        names = _project_task_names()
        # Sanity: discovery actually found the apps' tasks.
        for expected in (
            "providers.tasks.keepalive_sessions",
            "phone_refresh.tasks.run_refresh_job",
            "sales.tasks.run_charge_job",
            "sms_gateway.tasks.sweep_stuck_inbound_sms",
        ):
            self.assertIn(expected, names)
        queues = set(settings.CELERY_WORKER_QUEUES)
        for name in sorted(names):
            with self.subTest(task=name):
                self.assertIn(_routed_queue(name), queues)

    def test_every_beat_task_is_a_registered_task(self):
        names = _project_task_names()
        for entry in settings.CELERY_BEAT_SCHEDULE.values():
            self.assertIn(entry["task"], names)


class ProductionRedisRequiredTests(SimpleTestCase):
    """Production must never silently fall back to eager Celery / fakeredis."""

    def _import_production(self, **extra_env):
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("REDIS_URL", "DATABASE_URL")
        }
        env.update(
            DJANGO_SETTINGS_MODULE="config.settings.production",
            DJANGO_SECRET_KEY="x" * 50,
            DJANGO_ALLOWED_HOSTS="example.com",
            DJANGO_CSRF_TRUSTED_ORIGINS="https://example.com",
            DATABASE_URL="postgres://u:p@localhost:5432/db",
            PROVIDER_SECRETS_KEY="TpuHV7OrT9MVaVTE5UMgGlv-ysyL5xLPfy-SHlJ6un0=",
        )
        env.update(extra_env)
        # ``-c`` keeps "test" out of sys.argv, so the non-test branch is used.
        return subprocess.run(
            [sys.executable, "-c", "import config.settings.production as s; print(s.REDIS_URL)"],
            cwd=str(BASE_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_missing_redis_url_fails_fast(self):
        proc = self._import_production()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("REDIS_URL", proc.stderr)

    def test_redis_url_present_boots(self):
        proc = self._import_production(REDIS_URL="redis://:pw@redis:6379/0")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("redis://", proc.stdout)
