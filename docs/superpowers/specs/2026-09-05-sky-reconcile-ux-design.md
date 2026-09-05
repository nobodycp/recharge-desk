# Sky reconcile UX clarity — Design

**Date:** 2026-09-05  
**Status:** Approved (approach 1)

## Goal

Operators stop getting lost when reviewing Sky matching: clear where to start, why a gap exists, and settlements separated from actionable issues.

## UX

1. **Priority strip** (3 clickable cards): Action needed | Cost differences | Info (settlements)
2. **One panel at a time** via Bootstrap tabs (also: other supplier, matched, anomalies under Action/Info as appropriate)
3. **Per-row reason** short Arabic/English sentence + optional eSIM/cost hint when `|gap|` suggests it
4. **Expanded compare**: Sky charge lines vs RD sales (date, cost, product, eSIM) without digging Layan-labelled breakdown

## Scope

Templates + small enrichments on `SkyReconcilePhoneRow` / reconcile helpers. No change to matching math.
