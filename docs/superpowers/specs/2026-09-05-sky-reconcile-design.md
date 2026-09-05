# Sky report matching (مطابقة كشف سكاي) — Design

**Date:** 2026-09-05  
**Status:** Approved for implementation (approach 1; user: live fetch, login account only, settlements excluded from deficit)

## Problem

Layan companies already reconcile supplier charges via Excel upload (`layan_reconcile`). Sky charges live in the Sky Sales portal report **بيانات رصيد** (template `22`). Operators need the same mismatch categories without uploading a file: pull the report for a chosen date range and compare to Recharge Desk sales for the Sky company.

Primary risks to surface:

- Number charged on Sky but not recorded in RD
- Number recorded in RD but missing from Sky
- Over/under deduction vs RD `cost_price_snapshot`
- Charge + disconnect cycles (settlements) — show, but do not inflate estimated deficit
- Split eSIM charges on Sky (package then later eSIM fee) while RD deducts once (cost already includes `ESIM_EXTRA_COST`)

## Decisions (locked)

| Topic | Choice |
|-------|--------|
| Approach | Clone Layan UI/flow; replace Excel with live Sky API |
| Sky account | Logged-in env credentials only (`SKY_SALES_*` / `client_from_env`) |
| Report persistence | None — fetch live every run |
| Settlements in deficit | Display only; **excluded** from estimated deficit |
| Period | User-selected `period_from` / `period_to` (day / week / month). Default suggestion for first use: full August of current year when opening with empty dates is optional; form always editable |
| Compare amount | Sky supplier cost (absolute of signed delta / cost fields) vs RD `cost_price_snapshot` (includes eSIM +5 when applicable) |
| Phone key | Same `norm_phone` as Layan |

## Architecture

```
[Management UI] company detail → Sky report matching
        │
        ▼
[Form] period_from, period_to, min_amount_diff (default like Layan)
        │
        ▼
[View] sky_reconcile → fetch report 22 → reconcile_sky_report(...)
        │
        ├─ phone_refresh.providers.sky_sales_client
        │     fetch_balance_report(from, to) via GetEot4RReportData
        │     wildcards: [22, eot_user_id, DD/MM/YYYY, DD/MM/YYYY]
        │
        └─ companies/sky_reconcile.py
              parse rows → aggregate per phone → categorize vs Sale
```

No new DB models. No caching of Sky payloads.

## Components

### 1. `SkySalesClient.fetch_balance_report(date_from, date_to) -> list[dict]`

- Uses existing `generic_api("GetEot4RReportData", [22, eot_user_id, from_s, to_s])`.
- Ensures session via existing login/TOTP path (`client_from_env` / `ensure_session`).
- Returns `data` list of row dicts; raises clear `SkySalesError` on failure.
- Dates formatted `DD/MM/YYYY`.

### 2. `companies/sky_reconcile.py`

**Row mapping (template 22):**

| Field | Use |
|-------|-----|
| `ext_return_value_1` | Datetime |
| `ext_return_value_2` | Note / name (detect `esim` case-insensitive) |
| `ext_return_value_3` | Op type (`شحن`, disconnect/refund markers, …) |
| `ext_return_value_6` | Phone |
| `ext_return_value_11` | Balance before |
| `ext_return_value_12` | Signed delta |
| `ext_return_value_13` | Balance after |
| `ext_return_value_14` | Absolute cost (prefer for aggregation when present) |

**Aggregation per phone:**

- Sum all Sky cost movements that affect balance (`before ≠ after`), using `abs(delta)` for charges and negative for refunds/disconnect credits when delta is positive refund.
- Prefer signed `ext_return_value_12` for direction; fall back to op-type heuristics.
- **eSIM:** multiple Sky lines for same phone in period are **summed** before compare to a single RD sale cost.
- Skip deposit-like ops that are not phone charges if identifiable; otherwise rely on phone empty → ignore for phone buckets.

**Settlement detection:**

- Same phone has charge(s) and later disconnect/refund cycle in the period (mirror Layan `had_settlement` spirit using Sky op types / sign flips).
- Pure settlement with no leftover recharge and **no RD sale** → `split_settlements` only; **not** in `not_recorded`; **not** in estimated deficit.
- If settlement leftover recharge remains and no RD sale → treat leftover as `not_recorded` (actionable).

**Categories (UI sections):**

1. `not_recorded` — in Sky, not in this company’s RD sales (actionable; enters deficit)
2. `rd_only` — in RD for this Sky company, not in Sky report
3. `logged_other_supplier` — Sky charge present; sale exists under another company
4. `split_settlements` — charge + disconnect; informational
5. `amount_mismatches` — both sides present and `|sky_match − rd_net|` > min threshold
6. `matched` — OK (collapsed / secondary like Layan)
7. `balance_anomalies` — per-line: `before + delta ≠ after` (tolerance 0.01), or impossible jumps; listed separately for investigation

**Estimated deficit:** sum of actionable gaps from `not_recorded` + positive gaps from `amount_mismatches` (over-charge on Sky vs RD). Exclude settlement totals. Optionally include `rd_only` as reverse risk (money recorded locally without Sky) shown as separate KPI, not mixed into “Sky deficit”.

**KPIs:** row count, period, estimated deficit (Sky-side), RD-only total, optional end-balance from last row’s `after` vs RD ledger end (informational gap).

### 3. UI

- Gate: `company_supports_sky_reconcile(company)` → `phone_refresh_provider == "sky"` or name contains `"sky"` (parallel to Layan).
- Route: `/management/companies/<pk>/sky-reconcile/`
- Template: clone Layan layout; no file upload; copy explains live Sky pull.
- Link from `company_detail.html` when Sky.
- Reuse Layan partials where field names align, or thin Sky-specific partials if labels differ (“Sky” vs “Layan”).

### 4. Form

- `period_from`, `period_to` (required for practical runs; allow month-long August 2026 as first default in view context helper if desired)
- `min_amount_diff` (Decimal, default `3` like Layan)
- No Excel, no pending-credits unless trivial to port later (out of scope v1)

### 5. Errors

- Missing env credentials → friendly message, no stack trace in UI
- Sky API / OTP failure → message + empty result
- Empty report for range → result with zero rows, clear note

## Testing

- Unit tests for row parse, phone aggregate (incl. two-line eSIM sum), settlement exclusion from deficit, amount mismatch threshold, balance anomaly detection — **mocked** Sky rows (no live network).
- View gate: non-Sky company → redirect/403 like Layan.
- Optional smoke: live August fetch behind env flag (not required in CI).

## Out of scope (v1)

- Saving Sky report snapshots
- Multi-account / sub-dealer uid selection
- Excel fallback upload
- Auto-create missing sales
- Changing Layan reconcile behavior

## Success criteria

- From Sky company page, choose 2026-08-01 → 2026-08-31, run matching, see categorized phones without uploading a file.
- eSIM split lines compare as one summed Sky cost vs one RD `cost_price_snapshot`.
- Settlements visible and excluded from estimated deficit.
- Amount over/under vs RD cost flagged above threshold.
