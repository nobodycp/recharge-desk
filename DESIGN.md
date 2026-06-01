# Recharge Desk Design System

This file is the source of truth for new UI work in the project. The goal is a calm, compact, RTL-first management interface that feels consistent across sales, customers, employees, companies, inventory, reports, and settings.

## Design Direction

- Use a clean SaaS dashboard style: light surfaces, soft borders, compact spacing, subtle shadows, and one clear primary accent.
- Prefer density over empty space. The app is operational and data-heavy; screens should show useful information without feeling cramped.
- Keep interactions predictable. Lists, filters, sorting, pagination, buttons, and mobile cards should behave the same everywhere.
- RTL must be first-class. Avoid physical left/right decisions in new CSS unless required; prefer Bootstrap utilities and logical properties.

## Core Files

- `static/css/design-system.css` owns tokens, layout, cards, tables, filters, buttons, badges, navigation, and compatibility styling.
- `static/css/app.css` is for specialized product flows only, especially employee sale-entry chips and page-specific behavior.
- `templates/base_management.html` is the shell for management pages.
- `templates/partials/filter_card_open.html` and `templates/partials/filter_card_close.html` are the only pattern for collapsible filters.
- `templates/partials/pagination.html` is the only pagination pattern.

## Layout

- Every management page starts with:
  - `.rd-page-header`
  - `.rd-heading-xl`
  - optional `.rd-text-muted` subtitle
  - action buttons grouped in `d-flex flex-wrap gap-2`
- Main content should use Bootstrap grid with `g-3` as the default gap.
- Avoid inline widths and styles. If a max-width is needed, use a small wrapper and document why.
- Do not introduce new page shells unless there is a distinct user mode like employee sales entry.

## Cards

- New surfaces must use `.rd-card`.
- Card content uses `.rd-card-body`; headers use `.rd-card-header`.
- Legacy `.card` and `.app-card` are visually normalized in CSS, but new templates should not use them.
- Use `.rd-stat` only for KPI cards that need visual emphasis.
- Avoid large empty cards. Combine related small metrics when the information belongs together.

## Filters

- Filters belong above the data, not inside table headers.
- Use:
  - `{% include "partials/filter_card_open.html" with active_count=... indicator_id=... %}`
  - a `form.row.g-3.align-items-end`
  - `{% include "partials/filter_card_close.html" %}`
- Add hidden `sort` / `order` fields when the filtered data is sortable.
- Controls should usually be `form-control-sm` / `form-select-sm`.
- Keep filters on one row on wide screens when the fields fit.
- Reset buttons are `btn btn-outline-secondary btn-sm`.

## Data Grids

Use this structure for data-heavy lists:

```html
<div class="rd-card overflow-hidden rd-datagrid" id="...">
  <div class="rd-datagrid-toolbar rd-card-header py-2 d-flex flex-wrap align-items-center justify-content-between gap-2">
    <span>Title</span>
  </div>
  <div class="rd-table-dual rd-datagrid-body">
    <div class="d-none d-md-block rd-data-shell">
      <div class="table-responsive border-0">
        <table class="table mb-0 align-middle rd-table-modern">
          ...
        </table>
      </div>
    </div>
    <div class="d-md-none rd-mobile-stack p-2">
      ...
    </div>
  </div>
  {% include "partials/pagination.html" with page_obj=page_obj %}
</div>
```

- Desktop tables use `.rd-table-modern`.
- Mobile views use `.rd-mobile-row-card`; do not rely on horizontal table scrolling on phones.
- Empty desktop table rows should contain `<div class="empty-state border-0 mb-0">...`.
- Use `.tabular-nums` for all numbers, balances, dates, and references.

## Sorting

- Sortable headers must use `.rd-datagrid-sort`.
- The active header owns `is-active is-asc` or `is-active is-desc`.
- The selected header and arrow must update through the server-rendered response, exactly like the sales table.
- Do not pass stale sort/order values through `hx-vals`; let the clicked header URL be the source of truth.
- When changing sort, reset the relevant page number to `1`.

## Buttons

- Primary action: `btn btn-primary`.
- Secondary navigation/export/edit: `btn btn-outline-secondary`.
- Destructive permanent delete: `btn btn-outline-danger`.
- Cancel/void actions that are reversible should not be red unless they are destructive.
- Keep table action buttons `btn-sm`.
- Avoid `btn-link` for operational actions unless it is clearly a low-emphasis text action.

## Badges And Status

- Use `.rd-badge` variants:
  - `.rd-badge--paid` for success/active/paid.
  - `.rd-badge--pending` for waiting states.
  - `.rd-badge--cancelled` for failed/cancelled/destructive status.
  - `.rd-badge--neutral` for inactive, metadata, and plain labels.
- Avoid raw Bootstrap badge colors in management pages.

## Forms

- Forms inside cards use `d-grid gap-2` for simple vertical forms or `row g-3 align-items-end` for filter/search bars.
- Labels should remain visible; use visually hidden labels only in dense toolbars where the context is obvious.
- Help text should be short. Do not place long explanatory text between compact fields.
- Use `novalidate` on custom forms where server-rendered errors are expected.

## Tabs

- Use `.rd-section-tabs` with Bootstrap `nav nav-tabs`.
- Tabs switch major page sections only. Do not use tabs for small filter changes.
- Tab content should preserve the same grid/table/filter patterns as standalone pages.

## HTMX

- Use HTMX for list filtering, sorting, pagination, and small in-place actions.
- `hx-target` should point at the data grid container, not a random inner table, unless deliberately replacing one row.
- After actions that affect summary cards or balances, redirect or refresh the whole relevant page section so numbers stay synchronized.
- For irreversible operations, always include `hx-confirm` or a normal `onsubmit` confirmation.

## Mobile

- Every important desktop data table needs a mobile card representation.
- Mobile cards should show: main identifier, status/badge, key money amount, and primary actions.
- Avoid putting all columns into mobile cards; choose the most operationally useful values.

## Copy And Language

- Use Django `{% trans %}` for UI strings.
- Arabic labels should be clear and operational, not decorative.
- Keep titles short and subtitles explanatory.
- Avoid mixing untranslated hardcoded Arabic with translated strings unless the section is intentionally Arabic-only and stable.

## Do Not

- Do not add a new visual style for one page when an existing `rd-*` component fits.
- Do not use raw `.card`, `.table-sm`, `.table-light`, or Bootstrap badges in new management UI.
- Do not hide filters inside table toolbars.
- Do not add large empty whitespace to “balance” a layout.
- Do not use inline style for repeated visual patterns; add or reuse a class in `design-system.css`.
