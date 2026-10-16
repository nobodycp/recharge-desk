# Pearl legacy sweep — remaining kit migration

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline; one final review). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clear every remaining `LEGACY_*` offender for management/employee Pearl kit migration (except `phone_refresh/public_refresh.html`, which is out of scope per DESIGN).

**Architecture:** Template + light form/view glue only. Preserve URLs, GET/POST names, htmx contracts, displayed data. One branch `pearl-legacy-sweep`. Seven area tasks; commit per area; full suite before merge; one whole-branch review at the end.

**Tech Stack:** Django 4.2, `{% load kit %}`, `rd_page_header` / `rd_filter_card` / `rd_fields`, Pearl tokens, gettext ar.

**Spec:** Owner direction 2026-10-16 — migrate all remaining LEGACY pages unattended; review once at the end; merge locally to main when green.

## Global Constraints

- Never touch `db.sqlite3`.
- Never remove displayed information, links, or actions.
- Keep URL names, filter GET names, pagination keys, htmx targets/ids, `base_template` dual shell where used (inventory).
- Colours from `--rd-*` only; no `btn-warning` for warnings; no `table-light`; no new `badge bg-*` / `text-bg-*`.
- Shrink-only `LEGACY_*` in `core/tests_ui_conventions.py`; never add.
- `hx-confirm`: prefer autoescape / plain `{% trans %}`; avoid `|force_escape` double-escape.
- Out of scope: `phone_refresh/templates/phone_refresh/public_refresh.html`.
- Reference: `templates/companies/company_list.html`, `company_form.html`, `customers/customer_list.html`.
- Python: repo `.venv/bin/python`. Worktree: `.worktrees/pearl-legacy-sweep`.
- Do not commit `.superpowers/` scratch.

## Review Focus

- Inventory `base_template` + `_nav.html` still work for management and employee shells.
- Sales employee_entry fits one screen / touch targets; no behaviour change to charge flows.
- Reports dashboard period switch + htmx swap (`#rd-dashboard`) still works after kit header.
- phone_refresh forms keep secrets write-only patterns if present.
- sms_gateway hex offender moved to token.

---

### Task 1: Inventory (7 pages)

**Files:** `templates/inventory/{overview,main_stock,movements,cards,line_detail,customer_list,customer_detail}.html`; forms/views glue as needed; `core/tests_ui_conventions.py`; create `inventory/tests_kit.py`.

**LEGACY shrink:** PAGE_HEAD (all 7), LABEL_TAG (`cards.html`).

- [ ] Kit headers on all 7; keep `_nav.html` includes and dual `base_template`.
- [ ] Filters → `rd_filter_card` where form-shaped; keep GET names/ids.
- [ ] Forms → `rd_fields` where applicable; strip Bootstrap widget classes.
- [ ] Tests + commit `feat(inventory): Pearl page kit for stock screens`

### Task 2: Reports (7 pages)

**Files:** `templates/reports/{dashboard,employee_report,phone_lines_report,profit_report,provider_operations,sales_report,stale_phone_edit}.html`; glue; LEGACY; `reports/tests_kit.py` or extend existing.

**LEGACY:** PAGE_HEAD (7), LABEL_TAG (phone_lines, sales_report, stale_phone_edit), INLINE_WIDTH (stale_phone_edit).

- [ ] `rd_page_header` on each; dashboard keeps period switch / htmx contract.
- [ ] Filters/forms on kit; no info removed.
- [ ] Commit `feat(reports): Pearl page kit for report screens`

### Task 3: Sales management

**Files:** `management_sale_list.html`, `pending_payments.html`, `awaiting_approvals.html`, `sale_edit.html`, `partials/management_sale_row.html`; LEGACY PAGE_HEAD/LABEL_TAG/INLINE_WIDTH.

- [ ] Kit list/filter/edit; keep export, charge badges, row partial contracts.
- [ ] Commit `feat(sales): management sales screens on the page kit`

### Task 4: Sales employee

**Files:** `employee_entry.html`, `employee_payment_edit.html`, `employee_recent.html`, `employee_record_payment.html`, `employee_refresh_number.html`, `employee_sale_edit.html`, `employee_stock_check.html`.

- [ ] Kit headers/forms; preserve entry UX density and POST names.
- [ ] Commit `feat(sales): employee sales screens on the page kit`

### Task 5: Employees my_balance

**Files:** `templates/employees/my_balance.html`.

- [ ] `rd_page_header` (or kit-compatible header satisfying PAGE_HEADER regex); keep stats/ledger content.
- [ ] Commit `feat(employees): my_balance on the page kit`

### Task 6: phone_refresh (admin UI)

**Files:** All LEGACY phone_refresh templates except `public_refresh.html` — api_*, message_form, providers_index, reports, rule_form, settings_index, status_form, plus LABEL_TAG tabs `_general_tab` / `_providers_general_tab` as needed.

- [ ] Kit headers/forms; shrink PAGE_HEAD, APP_CARD, LABEL_TAG, INLINE_WIDTH for those files.
- [ ] Commit `feat(phone_refresh): admin screens on the page kit`

### Task 7: sms_gateway + leftover hex

**Files:** `templates/sms_gateway/{reports,settings_index}.html`; clear `LEGACY_HEX` for settings.

- [ ] Kit + tokens; commit `feat(sms_gateway): reports and settings on the page kit`

### Finish

- [ ] Full `manage.py test` green.
- [ ] `LEGACY_PAGE_HEAD` / APP_CARD / LABEL_TAG / INLINE_WIDTH / HEX empty or only intentional empty sets.
- [ ] Whole-branch code review (fresh agent).
- [ ] Merge to `main` locally; remove worktree; report done to owner.
