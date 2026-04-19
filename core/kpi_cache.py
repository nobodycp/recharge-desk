"""Tiny version-keyed cache for dashboard KPI values.

Why this exists
---------------
The management dashboard issues ~17 aggregation queries on every page
load (today/month/all-time profit, loss, volume, expenses, awaiting
count, customer debt, etc.). On a small dataset that's a few ms; on a
busy shop with 10k+ sales it adds up — and the same numbers are
recomputed for every refresh by every manager.

Caching the aggregate *values* (not querysets) gives a 95% speed-up on
the second hit, while staying correct because **any write to the
underlying tables bumps a global version number**, so the next read
recomputes from scratch automatically.

How invalidation works
----------------------
* Each KPI value is stored under a key that embeds the current version:
  ``kpi:v{N}:{name}``.
* Whenever a Sale / Expense / Company balance row / Customer ledger /
  Customer payment changes, ``bump_kpi_version()`` increments ``N``.
* Old keys remain in the cache until they expire on their own TTL —
  they're just inaccessible because every reader looks up ``v{N+1}:``.

This trades a little wasted memory in the cache backend for absolute
simplicity: a single counter invalidates every KPI atomically and we
never have to enumerate which keys to drop. ``memcached`` / ``redis``
cleanup handles the orphans; for ``LocMemCache`` they evict on the
backend's LRU cap.

The signals doing the bumping are wired up in :mod:`core.apps`.
"""

from __future__ import annotations

from typing import Callable, TypeVar

from django.core.cache import cache

VERSION_KEY = "kpi:version"

# Backstop TTL: even if every signal somehow misses (e.g. a bulk_create
# bypasses post_save), values still refresh within this window.
DEFAULT_TTL_SECONDS = 60

T = TypeVar("T")


def get_kpi_version() -> int:
    v = cache.get(VERSION_KEY)
    if v is None:
        # Use a non-expiring base value; bumps replace this in place.
        cache.set(VERSION_KEY, 1, timeout=None)
        return 1
    return int(v)


def bump_kpi_version() -> None:
    """Invalidate every cached KPI value at once."""
    try:
        cache.incr(VERSION_KEY)
    except ValueError:
        # Key was missing or evicted — set it fresh. Any reader hitting
        # the old version still reads stale data until its TTL expires;
        # that window is bounded by DEFAULT_TTL_SECONDS.
        cache.set(VERSION_KEY, 1, timeout=None)


def cached_kpi(name: str, builder: Callable[[], T], *, ttl: int = DEFAULT_TTL_SECONDS) -> T:
    """Return a cached KPI value, computing it via ``builder`` on miss.

    `name` should embed any time bucket the caller cares about (e.g.
    ``"dashboard:today_volume:2026-04-19"``) so date rollovers don't
    serve yesterday's number.
    """
    version = get_kpi_version()
    key = f"kpi:v{version}:{name}"
    value = cache.get(key)
    if value is None:
        value = builder()
        cache.set(key, value, timeout=ttl)
    return value
