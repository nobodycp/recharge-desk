# Customers — Pearl kit design

**Date:** 2026-10-16  
**Status:** Approved (chat design sections 1–4)

## Intent

Migrate management customer screens onto the Pearl page kit end-to-end so they match companies/employees: kit header, filter/forms, tokens, and shrink-only `LEGACY_*` cleanup — without changing behaviour or removing displayed information.

## Scope

In scope:

- List: [`templates/customers/customer_list.html`](../../templates/customers/customer_list.html)
- Form: [`templates/customers/customer_form.html`](../../templates/customers/customer_form.html) + [`customers/forms.py`](../../customers/forms.py) `CustomerForm` widgets
- Detail: [`templates/customers/customer_detail.html`](../../templates/customers/customer_detail.html) + payment/adjustment form polish for `rd_fields`
- Statement: [`templates/customers/customer_statement.html`](../../templates/customers/customer_statement.html)
- Detailed invoice: [`templates/customers/customer_detailed_invoice.html`](../../templates/customers/customer_detailed_invoice.html)
- Payment submissions: [`templates/customers/payment_submissions_list.html`](../../templates/customers/payment_submissions_list.html)
- Shrink matching entries in [`core/tests_ui_conventions.py`](../../core/tests_ui_conventions.py) `LEGACY_*` when each template is clean

Out of scope:

- Inventory customer pages (`templates/inventory/customer_*`)
- Changing write-off / delete / payment / ledger business logic
- Print letterhead partial redesign (`_invoice_letterhead.html`, `_invoice_summary.html`, `_invoice_signature.html` stay; only drop `table-light` from page tables)

## Approach

One feature branch (`pearl-customers`), five SDD tasks (list → form → detail → statement+invoice → payment submissions), each with tests and review before the next.

## 1) Customer list

- Wrap in `rd-page`; `{% rd_page_header %}` with title “Customers”, subtitle “Customers buying on account and their balances.”
- Keep Export CSV, Export payments CSV (`hx-boost="false"`), New customer.
- Keep debt/credit KPI cards (prefer `rd-stat` if natural; no metric removed).
- Search via `rd_filter_card` with GET name `q`; keep input id `cust-q` (or equivalent form field with same name).
- Table/mobile dual grid unchanged in columns and data; Active badges stay `rd-badge`.
- **Write off:** keep when `current_balance > 0`; button must **not** use `btn-warning` / orange — use `btn-outline-danger` or `btn-outline-secondary`. Confirm text unchanged.
- Delete stays `btn-outline-danger`. Prefer plain `{% trans %}` in `hx-confirm` (no `|force_escape` double-escape); autoescape is enough after the kit confirm fix.
- Prefer `row_actions` where it fits Edit + conditional write-off/delete without losing forms; otherwise keep explicit action forms with the same URLs.
- Shrink `LEGACY_PAGE_HEAD` for `customer_list.html`.

## 2) Customer form

- `rd-page rd-page--form` + `rd_page_header` (title from view, back to customer list).
- `{% rd_fields form %}` + `rd-form-actions` Save/Cancel; keep `form_errors_toast` for non-field errors.
- Strip hand-written Bootstrap classes from `CustomerForm` / `initial_phone` widgets (kit supplies them); keep checkbox `role=switch` if the project pattern uses it on similar forms.
- Remove inline `max-width` wrapper; shrink `LEGACY_PAGE_HEAD`, `LEGACY_LABEL_TAG`, `LEGACY_INLINE_WIDTH` for `customer_form.html`.

## 3) Customer detail

- `rd-page` + `rd_page_header` (customer name, subtitle “Customer account”, Inactive note without orange/`text-warning` — use muted/`rd-badge--neutral` or ink secondary).
- Keep Statement, Detailed invoice, Back actions.
- All KPI cards retained (balance / awaiting / pending); prefer `rd-stat` patterns.
- Sales and ledger tables: add `rd-table-modern` (or project equivalent); **keep** row ids `customer-sale-row-{{ pk }}`, `customer-ledger-row-{{ pk }}`, `customer-payment-row-{{ pk }}` and all htmx delete contracts (`hx-post`, targets, swaps, confirms).
- Payment and adjustment forms via `rd_fields` (or field helpers); strip Bootstrap classes from `CustomerPaymentForm` / `CustomerAdjustmentForm` / `CustomerPhoneForm` widgets as needed.
- SIM stock, phones (add/remove), recent payments: same information and URLs; polish only.
- Shrink `LEGACY_PAGE_HEAD` and `LEGACY_LABEL_TAG` for `customer_detail.html`.

## 4) Statement + detailed invoice + payment submissions

**Statement / detailed invoice (screen + print):**

- Screen chrome: `rd_page_header` + same actions (Back, CSV where present, Print, cross-links); keep `d-print-none` on toolbars/filters.
- Period filter: kit/`rd_filter_card` or unified fields; GET names `period_from` / `period_to`; keep ids `cs-from` / `cs-to` and `di-from` / `di-to`.
- Keep letterhead, summary strip, `rd-print-flow`, invoice partials, chronological content.
- Remove every `table-light`; use `rd-table-modern` / `rd-*` summary rows. Shrink `LEGACY_TABLE_LIGHT` and `LEGACY_PAGE_HEAD` for both templates.

**Payment submissions:**

- `rd_page_header` + back to customers list.
- Search → `rd_filter_card`; GET `q`; keep id `pay-sub-q` if practical.
- Table/mobile and approve/reject actions unchanged.
- Shrink `LEGACY_PAGE_HEAD` for `payment_submissions_list.html`.

## Contracts that must not break

- URL names under `customers:` (list, create, edit, detail, write_off, delete, statement, statement_csv, detailed_invoice, payment submissions approve/reject, record payment/adjustment, phones, payment/ledger delete)
- GET: `q`, `period_from`, `period_to`, pagination keys (`sales_page`, `ledger_page`, `phones_page`)
- Element ids: `cust-q`, `cs-from`/`cs-to`, `di-from`/`di-to`, `pay-sub-q`, sale/ledger/payment row ids
- Prefetch/query behaviour on customer list (phones prefetch regression test)
- Write-off and delete confirm copy (English msgid; Arabic via gettext)

## Success criteria

- Kit conventions tests green; listed customer templates removed from their `LEGACY_*` sets when clean
- Existing `customers` + `core` tests green; full suite green before merge menu
- Screenshots 375 + 1280, light + dark, under `.superpowers/customers-2026-10-16/` for list, form, detail, statement, submissions
