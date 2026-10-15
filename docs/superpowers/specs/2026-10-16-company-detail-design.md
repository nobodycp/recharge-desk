# Company detail — Pearl kit design

**Date:** 2026-10-16  
**Status:** Approved (chat design sections 1–4)

## Intent

Migrate the management company detail page onto the Pearl page kit end-to-end so it matches company list/forms and employee detail: kit header, tokens, filter/forms conventions, and shrink-only `LEGACY_*` cleanup — without changing behaviour or removing information.

## Scope

In scope:

- Shell: [`templates/companies/company_detail.html`](../../templates/companies/company_detail.html)
- General: [`templates/companies/partials/company_detail_general.html`](../../templates/companies/partials/company_detail_general.html)
- Connection: [`templates/companies/partials/company_detail_connection.html`](../../templates/companies/partials/company_detail_connection.html) plus i18n of hard-coded Arabic in forms/connection helpers
- Shared Sales/Ledger grids: [`templates/reports/partials/company_report_sales.html`](../../templates/reports/partials/company_report_sales.html), [`company_report_ledger.html`](../../templates/reports/partials/company_report_ledger.html)

Out of scope:

- Dead `company_report.html` cleanup
- Kit `force_escape` double-escape fix
- Reconcile page changes

## Approach

One feature branch (`pearl-company-detail`), four implementation tasks (shell → general → connection → sales/ledger partials), each with tests and review before the next.

## Shell

- Wrap in `rd-page`; use `{% rd_page_header %}` with company name, subtitle “Supplier balance and sales”, back to company list.
- Keep Edit and conditional Layan/Sky reconcile links.
- Keep `rd-section-tabs` and existing `?tab=` / `querystring` hrefs.
- Connection tab label via `{% trans %}` (Arabic msgstr preserves current UI under ar).
- Preserve `#company-sales-grid` / `#company-ledger-grid` wrappers and exact `hx-vals`.

## General

- Period filter on kit (`rd_filter_card` or equivalent) with GET names `period_from`, `period_to`, hidden `tab=general`; keep `cr-period-from` / `cr-period-to` ids.
- All KPI metrics retained; prefer `rd-stat` / existing card patterns.
- Deposit and adjustment forms via `rd_fields`; submit buttons remain `dep-submit` / `adj-submit` value `1`; prefixes `dep` / `adj` unchanged.
- Embedded sales/ledger includes keep current htmx contracts (`tab=general`).

## Connection

- UI and flash strings via gettext/`{% trans %}` with Arabic msgstr equal to today’s pinned strings.
- Remove `var(--bs-body-bg)` inline styles; use `--rd-*` / `rd-*` surfaces.
- Secrets stay write-only; POST actions and login-test UX unchanged.

## Sales / Ledger partials

- No column, sort, pagination, or sibling-state hidden field removed.
- Filter forms keep `sales_q` / `ledger_q` and existing `hx-*` targets/indicators.
- Replace inline `max-width` styles with token/class.
- Status via existing badge partials / `rd-badge`.

## Contracts that must not break

- URL name `companies:company_detail` and connection POST URL names
- Query: `tab`, `partial`, `period_*`, `sales_*`, `ledger_*`
- POST: `dep-submit`, `adj-submit`, deposit/adjustment field prefixes
- Element ids used by tests and htmx indicators
- Arabic strings asserted in `companies/tests_connection.py` (still present in rendered Arabic HTML)

## Success criteria

- Kit conventions tests green; listed templates removed from `LEGACY_*` when clean
- Existing company detail / connection / report tests green
- Screenshots 375 + 1280, light + dark, under `.superpowers/companies-2026-10-16/`
- Full test suite green before merge menu
