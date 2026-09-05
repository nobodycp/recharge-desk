# Sky Report Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add live Sky balance-report matching on the Sky company page (no Excel), comparable to Layan reconcile.

**Architecture:** Extend `SkySalesClient` with `fetch_balance_report`; add `companies/sky_reconcile.py` to parse/aggregate/categorize; mirror Layan view/form/template/URL gated for Sky companies. Live fetch each POST; settlements excluded from estimated deficit.

**Tech Stack:** Django 4.2, existing `SkySalesClient`, SQLite/Postgres via ORM, Bootstrap management templates.

## Global Constraints

- Reuse `norm_phone` from `companies.layan_reconcile` (do not fork phone rules).
- Credentials only via existing env / `client_from_env()` — no multi-uid UI.
- No persistence of Sky report payloads.
- Settlements: display only; excluded from `estimated_deficit`.
- Compare Sky summed cost vs RD `Sale.cost_price_snapshot` (non-cancelled) for the Sky company.
- Do not commit unless the user asks.

---

### Task 1: `fetch_balance_report` on SkySalesClient

**Files:**
- Modify: `phone_refresh/providers/sky_sales_client.py`
- Test: `phone_refresh/tests_sky_sales_client.py`

**Interfaces:**
- Produces: `SkySalesClient.fetch_balance_report(self, date_from: date, date_to: date) -> list[dict]`
- Uses: `generic_api("GetEot4RReportData", [22, eot_user_id, "DD/MM/YYYY", "DD/MM/YYYY"])`, `self._rows`

- [ ] **Step 1: Write failing test** — mock `generic_api`, assert wildcards `[22, uid, from, to]` and returned rows.
- [ ] **Step 2: Implement `fetch_balance_report`** — require `eot_user_id`; format dates; return `_rows(data)`.
- [ ] **Step 3: Run tests** — `python manage.py test phone_refresh.tests_sky_sales_client -v2`

---

### Task 2: Sky row parse + phone aggregation (unit)

**Files:**
- Create: `companies/sky_reconcile.py`
- Create: `companies/tests_sky_reconcile.py`

**Interfaces:**
- Produces:
  - `parse_sky_balance_rows(rows, period_from, period_to) -> tuple[dict[str, SkyPhoneAgg], int, list[BalanceAnomaly], Decimal|None]`
  - `SkyPhoneAgg` with `charges`, `refunds`, `net`, `lines`, `had_settlement`, `sky_match_amount`
  - Cost from signed `ext_return_value_12` (preferred) or `14`; eSIM note on `ext_return_value_2`; phone on `6`; op on `3`; before/after on `11`/`13`
  - Settlement: charge + refund/disconnect in period → `had_settlement`; pure settlement net excluded from deficit later
  - Balance anomaly when `|before + delta - after| > 0.01`

- [ ] **Step 1: Failing tests** for eSIM two-line sum, settlement flag, anomaly, period filter.
- [ ] **Step 2: Implement parse/aggregate.**
- [ ] **Step 3: Run** `python manage.py test companies.tests_sky_reconcile -v2`

---

### Task 3: `reconcile_sky_report` categories + deficit

**Files:**
- Modify: `companies/sky_reconcile.py`
- Modify: `companies/tests_sky_reconcile.py`

**Interfaces:**
- Produces: `reconcile_sky_report(company, rows, *, period_from, period_to, min_amount_diff) -> SkyReconcileResult`
- Categories: `not_recorded`, `rd_only`, `logged_other_supplier`, `split_settlements`, `amount_mismatches`, `matched`, `balance_anomalies`
- `estimated_deficit` = sum actionable `not_recorded` display amounts + positive amount-mismatch gaps only (no settlements)
- Reuse `_rd_sales_by_phone` / `_rd_ledger_totals` from `layan_reconcile` if importable without circular pain; else thin local copies

- [ ] **Step 1: Tests** for not_recorded, rd_only, other supplier, settlement excluded from deficit, amount mismatch threshold, eSIM sum match.
- [ ] **Step 2: Implement reconcile.**
- [ ] **Step 3: Run tests.**

---

### Task 4: Form, view, URL, templates, company link

**Files:**
- Modify: `companies/forms.py` — `SkyReportReconcileForm` (dates + min diff, no file)
- Modify: `companies/views.py` — `sky_reconcile`
- Modify: `companies/urls.py`
- Create: `templates/companies/sky_reconcile.html`
- Modify: `templates/companies/company_detail.html` — Sky link
- Modify: `companies/tests.py` or `companies/tests_sky_reconcile.py` — view gate + page renders
- Modify: `tools/fill_ar_translations.py` — AR strings for new UI labels

**Interfaces:**
- View POST: `client_from_env()` → ensure login → `fetch_balance_report` → `reconcile_sky_report`
- Catch `SkySalesError` → message; gate via `company_supports_sky_reconcile`

- [ ] **Step 1: Wire form/view/url/template/link.**
- [ ] **Step 2: View tests with mocked fetch.**
- [ ] **Step 3: Run companies + phone_refresh related tests.**

---

### Task 5: Manual August smoke (optional live)

**Files:** none (manual)

- [ ] With server running and `sky_lab/.env` loaded for the process, open Sky company → matching → `2026-08-01`–`2026-08-31` → verify sections populate.
- [ ] If session/OTP fails, fix env loading path so management process sees `SKY_SALES_*` (prefer `load_dotenv_sky()` at start of view fetch helper).

---

## Spec coverage check

| Spec item | Task |
|-----------|------|
| Live GetEot4RReportData 22 | 1, 4 |
| Login account only | 1, 4 |
| No save report | 4 |
| Categories + eSIM sum | 2, 3 |
| Settlements out of deficit | 3 |
| Balance anomalies | 2, 3 |
| UI like Layan + date range | 4 |
| August runnable | 4, 5 |
