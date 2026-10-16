# Customers — Pearl kit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put customer list, form, detail, statement, detailed invoice, and payment submissions on the Pearl page kit without changing URLs, GET/POST contracts, or htmx behaviour.

**Architecture:** Templates plus light form widget / filter-form glue. Views stay behaviour-identical. Five SDD tasks on branch `pearl-customers`. Reference: `templates/companies/company_list.html`, `company_form.html`, `company_detail.html`, `employees/employee_detail.html`.

**Tech Stack:** Django 4.2, `{% load kit %}`, Pearl tokens (`--rd-*`), htmx hx-boost, gettext Arabic.

**Spec:** docs/superpowers/specs/2026-10-16-customers-design.md

## Global Constraints

- Never touch `db.sqlite3`. Probe only: `PYTHONPATH=.superpowers DJANGO_SETTINGS_MODULE=probe_settings .venv/bin/python manage.py runserver 127.0.0.1:8765`.
- Never remove displayed information, links, or actions.
- Keep URL names, GET names (`q`, `period_from`, `period_to`), pagination keys, row ids (`customer-sale-row-*`, `customer-ledger-row-*`, `customer-payment-row-*`), filter ids (`cust-q`, `cs-from`/`cs-to`, `di-from`/`di-to`, `pay-sub-q`).
- Colours from `--rd-*` only. No `btn-warning` / orange for Write off (use `btn-outline-danger` or `btn-outline-secondary`). No `table-light`. No `text-warning` for Inactive on detail (muted / `rd-badge--neutral`).
- `hx-confirm`: prefer plain `{% trans %}` / autoescape; do not reintroduce `|force_escape` double-escape on confirms.
- Strings via `{% trans %}` / gettext + `locale/ar/LC_MESSAGES/django.po` + `compilemessages -l ar` when adding msgids.
- Shrink-only `LEGACY_*` in `core/tests_ui_conventions.py`; never add files to those sets.
- Full relevant suite before each task commit; full `manage.py test` before finish.

## Review Focus

- Customer list phones prefetch: `test_customer_list_does_not_n_plus_one_phones` (or current name) stays green.
- Write-off still posts to `customers:customer_write_off` and only shows when `current_balance > 0`.
- Detail htmx deletes still target the correct row ids and URLs.
- Statement/invoice print: letterhead + `d-print-none` toolbars still hide on print; period GET still filters.
- Payment submission approve/reject POST URLs and CSRF forms unchanged.

---

## File Structure

| File | Responsibility |
|---|---|
| `templates/customers/customer_list.html` | Kit list + filter + actions |
| `templates/customers/customer_form.html` | Kit form |
| `templates/customers/customer_detail.html` | Kit detail shell, KPIs, tables, side forms |
| `templates/customers/customer_statement.html` | Kit chrome + period filter; drop `table-light` |
| `templates/customers/customer_detailed_invoice.html` | Same for invoice |
| `templates/customers/payment_submissions_list.html` | Kit header + filter |
| `customers/forms.py` | Strip Bootstrap from widgets; optional search/period filter forms |
| `customers/views.py` (or equivalent) | Pass `card_form` / period forms if needed only |
| `customers/tests_kit.py` (create) | Kit assertions per surface |
| `core/tests_ui_conventions.py` | Shrink LEGACY sets |
| `locale/ar/LC_MESSAGES/django.po` | New msgids if any |

---

### Task 1: Customer list on the kit

**Files:**
- Modify: `templates/customers/customer_list.html`, `customers/forms.py` (optional `CustomerSearchForm` with `q`), `customers/views.py` (pass `card_form` if needed), `core/tests_ui_conventions.py`, locale if needed
- Test: `customers/tests_kit.py` (create)

**Interfaces:**
- Consumes: existing list view context (`page_obj`, `q`, `debt_total`, `credit_total`)
- Produces: list uses `rd_page_header` + `rd_filter_card`; Write off button class not `btn-warning`; `customer_list.html` removed from `LEGACY_PAGE_HEAD`

- [ ] **Step 1: Write failing tests** in `customers/tests_kit.py`:
  - `test_customer_list_uses_kit_header` — response contains `rd-page-head` (or kit header marker)
  - `test_write_off_not_btn_warning` — when a customer has positive balance, HTML has write-off control without `btn-warning`
  - Existing phones prefetch test still imported/run in suite
- [ ] **Step 2: Run** `.venv/bin/python manage.py test customers.tests_kit -v2` — expect FAIL
- [ ] **Step 3: Implement** kit header, filter, Write off styling, confirm without double-escape; shrink LEGACY_PAGE_HEAD
- [ ] **Step 4: Run** `.venv/bin/python manage.py test customers core -v1` — expect PASS
- [ ] **Step 5: Commit** `feat(customers): customer list on the page kit`

---

### Task 2: Customer form on the kit

**Files:**
- Modify: `templates/customers/customer_form.html`, `customers/forms.py` (`CustomerForm` widgets), `core/tests_ui_conventions.py`, locale if needed
- Test: extend `customers/tests_kit.py`

**Interfaces:**
- Consumes: `title`, `form` from create/edit views
- Produces: `rd_page--form`, `rd_fields`, no `.label_tag`, no inline `max-width`; remove form from `LEGACY_PAGE_HEAD`, `LEGACY_LABEL_TAG`, `LEGACY_INLINE_WIDTH`

- [ ] **Step 1: Failing tests** — create/edit pages use kit header + `rd-field` / kit field markup; POST still saves name/notes/is_active/initial_phone
- [ ] **Step 2: Run** — expect FAIL
- [ ] **Step 3: Implement**; strip widget Bootstrap classes
- [ ] **Step 4: Run** `manage.py test customers core`
- [ ] **Step 5: Commit** `feat(customers): customer form on the page kit`

---

### Task 3: Customer detail on the kit

**Files:**
- Modify: `templates/customers/customer_detail.html`, `customers/forms.py` (payment/adjustment/phone widgets), `core/tests_ui_conventions.py`, locale if needed
- Test: extend `customers/tests_kit.py`

**Interfaces:**
- Consumes: existing detail context (sales/ledger/phones pages, forms, sim_balances, payments)
- Produces: kit header; KPIs retained; `rd-table-modern` on main tables; `rd_fields` on payment/adjustment; row ids and htmx delete attrs preserved; Inactive without `text-warning`; remove from `LEGACY_PAGE_HEAD` + `LEGACY_LABEL_TAG`

- [ ] **Step 1: Failing tests** — kit header; sale row id present; no `text-warning` for inactive; payment form renders without `.label_tag` path (no raw label_tag in template)
- [ ] **Step 2: Run** — expect FAIL
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run** `manage.py test customers core sales` (htmx delete paths if covered)
- [ ] **Step 5: Commit** `feat(customers): customer detail on the page kit`

---

### Task 4: Statement + detailed invoice on the kit

**Files:**
- Modify: `templates/customers/customer_statement.html`, `templates/customers/customer_detailed_invoice.html`, optional period form in `customers/forms.py` + view context, `core/tests_ui_conventions.py`
- Test: extend `customers/tests_kit.py`

**Interfaces:**
- Keep letterhead includes, print button, CSV/cross-links, period GET names and ids `cs-*` / `di-*`
- Remove all `table-light`; screen headers via `rd_page_header`; shrink `LEGACY_TABLE_LIGHT` (both files) and `LEGACY_PAGE_HEAD` for both

- [ ] **Step 1: Failing tests** — statement/invoice have kit header marker; response has no `table-light`
- [ ] **Step 2: Run** — expect FAIL
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run** `manage.py test customers core`
- [ ] **Step 5: Commit** `feat(customers): statement and invoice on the page kit`

---

### Task 5: Payment submissions list on the kit

**Files:**
- Modify: `templates/customers/payment_submissions_list.html`, optional search form + view glue, `core/tests_ui_conventions.py`
- Test: extend `customers/tests_kit.py`; screenshots; full suite

**Interfaces:**
- `rd_page_header` + Customers back link; `rd_filter_card` for `q`; approve/reject forms unchanged; remove from `LEGACY_PAGE_HEAD`
- Screenshots 375+1280 light/dark → `.superpowers/customers-2026-10-16/`
- Full `.venv/bin/python manage.py test`

- [ ] **Step 1: Failing test** — submissions list uses kit header
- [ ] **Step 2–4: Implement**, screenshots, full suite
- [ ] **Step 5: Commit** `feat(customers): payment submissions list on the page kit`

---

## Out of scope

- Inventory customer templates
- Business-logic changes to write-off, FIFO payment, or submissions
- Redesigning print letterhead partials beyond dropping `table-light` on page tables

## Execution

Worktree branch `pearl-customers` off `main`. SDD with implementer + task review per task; final branch review; finishing menu (merge/PR/keep). Owner already chose Subagent-driven (approach 1).
