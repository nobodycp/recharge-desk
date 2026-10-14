from __future__ import annotations

import datetime as dt

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from providers import jobs
from providers.models import ProviderJob
from providers.tasks import purge_old_jobs


def _make(**overrides):
    params = dict(kind="refresh", provider_key="sky", phone="0529000000", source="web")
    params.update(overrides)
    return jobs.create_job(**params)


class JobHelperTests(TestCase):
    def test_create_job_defaults(self):
        job = _make(payload={"ip": "1.1.1.1"})
        job.refresh_from_db()
        self.assertEqual(job.status, "queued")
        self.assertEqual(job.attempts, 0)
        self.assertEqual(job.payload, {"ip": "1.1.1.1"})
        self.assertIsNone(job.idempotency_key)

    def test_idempotency_key_is_unique_but_nullable(self):
        _make()
        _make()  # two NULL keys are fine
        _make(idempotency_key="k1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            _make(idempotency_key="k1")

    def test_mark_running_is_atomic_and_single_winner(self):
        job = _make()
        stale = ProviderJob.objects.get(pk=job.pk)
        self.assertTrue(jobs.mark_running(job))
        self.assertFalse(jobs.mark_running(stale))
        job.refresh_from_db()
        self.assertEqual(job.status, "running")
        self.assertEqual(job.attempts, 1)
        self.assertIsNotNone(job.started_at)

    def test_mark_done_and_failed_store_result_and_finish_time(self):
        done, failed = _make(), _make()
        jobs.mark_done(done, {"status": "refreshed"})
        jobs.mark_failed(failed, {"status": "error"})
        done.refresh_from_db()
        failed.refresh_from_db()
        self.assertEqual((done.status, done.result), ("done", {"status": "refreshed"}))
        self.assertEqual((failed.status, failed.result), ("failed", {"status": "error"}))
        self.assertIsNotNone(done.finished_at)
        self.assertIsNotNone(failed.finished_at)


class FindInflightTests(TestCase):
    def test_reuses_queued_and_running_within_window(self):
        queued = _make()
        self.assertEqual(jobs.find_inflight("refresh", "0529000000", 90), queued)
        jobs.mark_running(queued)
        self.assertEqual(jobs.find_inflight("refresh", "0529000000", 90), queued)

    def test_ignores_finished_jobs(self):
        job = _make()
        jobs.mark_done(job, {})
        self.assertIsNone(jobs.find_inflight("refresh", "0529000000", 90))
        failed = _make()
        jobs.mark_failed(failed, {})
        self.assertIsNone(jobs.find_inflight("refresh", "0529000000", 90))

    def test_ignores_jobs_older_than_window(self):
        job = _make()
        ProviderJob.objects.filter(pk=job.pk).update(
            created_at=timezone.now() - dt.timedelta(seconds=91)
        )
        self.assertIsNone(jobs.find_inflight("refresh", "0529000000", 90))

    def test_matches_kind_and_phone_exactly(self):
        _make()
        self.assertIsNone(jobs.find_inflight("charge", "0529000000", 90))
        self.assertIsNone(jobs.find_inflight("refresh", "0529000001", 90))
        self.assertIsNone(jobs.find_inflight("refresh", "529000000", 90))

    def test_returns_newest(self):
        old = _make()
        ProviderJob.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - dt.timedelta(seconds=30)
        )
        new = _make()
        self.assertEqual(jobs.find_inflight("refresh", "0529000000", 90), new)


class PurgeOldJobsTests(TestCase):
    def test_deletes_only_old_refresh_jobs(self):
        old_refresh = _make()
        old_charge = _make(kind="charge")
        recent_refresh = _make()
        cutoff = timezone.now() - dt.timedelta(days=31)
        ProviderJob.objects.filter(pk__in=[old_refresh.pk, old_charge.pk]).update(
            created_at=cutoff
        )
        purge_old_jobs()
        remaining = set(ProviderJob.objects.values_list("pk", flat=True))
        self.assertEqual(remaining, {old_charge.pk, recent_refresh.pk})
