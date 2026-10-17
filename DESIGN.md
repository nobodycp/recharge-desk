# Recharge Desk Design System — Pearl

This file is the source of truth for new UI work. Pearl is a calm, warm, RTL-first management interface: off-white paper, ink-black primary actions, one logo-red accent used sparingly, and clear numbers. `core/tests_ui_conventions.py` and `core/tests_design_tokens.py` enforce the parts that can be checked mechanically; run them after any UI change.

## Identity

- **Paper and ink.** Warm off-white page, white surfaces, near-black text. Dark mode is the same system on layered warm blacks, never pure `#000`.
- **Primary = ink.** `btn-primary` is black (white in dark mode). Orange is *not* the primary colour.
- **Logo red = accent only.** The accent is the Pro Sim wordmark red (`#d00000` light, `#ff3b3b` dark). Active dots, focus/progress cues, and at most one special action per screen (`btn-accent`, e.g. charge). Never for errors, warnings-as-fill, or decoration. Gold stays inside the logo.
- **Dense but quiet.** Operational, data-heavy screens: compact spacing, hairline borders, very light shadows, numbers that line up.
- **RTL first.** Use logical properties (`margin-inline-*`, `text-start`); no `ml-*`/`mr-*` in new CSS.

## Core Files

- `static/css/design-system.css` — tokens (light + dark blocks at the top, ending at `/* @end-tokens */`), layout, shell, components. Below the marker there is no raw hex.
- `static/css/app.css` — specialised product flows only (employee sale entry, dashboard period switch). No raw hex.
- `static/css/number-check.css`, `static/css/login.css` — page-specific; no raw hex.
- `static/css/fonts.css` — self-hosted `@font-face` (woff2 in `static/fonts/`).
- `templates/base_management.html`, `templates/base_employee.html` — the two shells. Every page extends one (see `.cursor/rules/ui-navigation-and-toasts.mdc`).
- `templates/partials/filter_card_open.html` / `filter_card_close.html` — the only collapsible-filter pattern. `templates/partials/pagination.html` — the only pagination.
- `templates/accounts/login.html` + `static/css/login.css` + `static/js/login.js` — the login page; reference for a full-bleed, token-only page.

## Tokens

Defined per theme in `design-system.css` (`[data-bs-theme="light"]` / `"dark"`). Use the token, never the value.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bs-body-bg` | `#fafaf8` | `#0b0b0a` | page background |
| `--rd-surface` | `#ffffff` | `#151514` | cards, inputs, popovers |
| `--rd-surface-2` | `#f4f4f2` | `#1f1f1d` | table headers, chips, skeletons, hover |
| `--bs-border-color` | `#ececea` | `#2a2a27` | hairline borders |
| `--rd-field-border` | `#8a8a86` | `#74746e` | borders of inputs, selects, checkboxes, entry fields (≥ 3:1 vs `--rd-surface`; hairline stays for cards/dividers) |
| `--rd-ink` | `#0a0a0a` | `#f5f5f2` | text, primary fills, focus outline |
| `--rd-muted` | `#6b6b68` | `#9a9a94` | secondary text (≥ 4.5:1) |
| `--rd-faint` | `#a3a39e` | `#6b6b66` | placeholders, decorative marks only |
| `--rd-accent` | `#d00000` | `#ff3b3b` | the accent fill (dots, progress, `btn-accent`); sampled from the logo red |
| `--rd-accent-soft` | `rgba(208,0,0,.10)` | `rgba(255,59,59,.15)` | accent tint behind small marks |
| `--rd-accent-ink` | `#9b0c14` | `#ff9a9a` | logo-red *text* (≥ 4.5:1) |
| `--rd-on-accent` | `#ffffff` | `#0a0a0a` | text on an accent fill |
| `--rd-primary-bg` / `--rd-primary-fg` | `#0a0a0a` / `#fff` | `#f5f5f2` / `#0a0a0a` | primary buttons |
| `--rd-success` / `-soft` | `#0a7a3e` / `#e8f6ee` | `#4ade80` / `rgba(74,222,128,.12)` | paid, up |
| `--rd-warning` / `-soft` | `#c2410c` / `#fff0e9` | `#fbbf24` / `rgba(251,191,36,.12)` | pending, down |
| `--rd-danger` / `-soft` | `#b91c1c` / `#fdecec` | `#f87171` / `rgba(248,113,113,.12)` | cancelled, failed, destructive |
| `--rd-surface-raised` | `#ffffff` | `#262624` | floating panels (user/bell menus, search results); lighter than page and cards in dark so a popover reads as raised |
| `--rd-shadow-card` / `--rd-shadow-xs` / `--rd-shadow-sm` / `--rd-shadow-md` / `--rd-shadow-lg` / `--rd-shadow-float` | ink-tinted, very light | inset hairline + deep shadows | elevation (card, controls, raised blocks, floating buttons) |
| `--rd-shadow-pop` | `0 16px 48px rgba(10,10,10,.1)` | deep shadow + 1px white-5 % ring | shadow of floating panels (pair with `--rd-surface-raised`) |
| `--rd-success-on-ink` / `--rd-warning-on-ink` / `--rd-danger-on-ink` | `#4ade80` / `#fbbf24` / `#f87171` | `#0a7a3e` / `#c2410c` / `#b91c1c` | status tones on an ink surface (the employee balance hero). Toasts follow the theme surface and use `--rd-success` / `--rd-warning` / `--rd-danger`. |
| `--rd-on-danger` | `#ffffff` | `#0a0a0a` | text on red count chips |
| `--rd-scrim` | `rgba(10,10,10,.32)` | `rgba(0,0,0,.6)` | drawer / modal dim |
| `--rd-hero-bg` / `--rd-hero-fg` / `--rd-hero-plate` / `--rd-hero-success` | `#0a0a0a` / `#fff` / `#fff` / `#4ade80` | `#050505` / `#f5f5f2` / `#f5f5f2` / `#4ade80` | login brand panel: stays ink in both themes; the plate is the letter-mark tile shown when no logo is uploaded (uploaded logos have no plate: the hero uses `logo_dark_url`) |
| `--rd-warning-ink` | `var(--rd-warning)` | `#fcd34d` | warning *text* on a soft warning tint |
| `--rd-info` / `--rd-info-soft` | `var(--rd-ink)` / `var(--rd-surface-2)` | same | info = neutral ink (Pearl has no blue) |
| `--rd-tone-1` / `--rd-tone-2` / `--rd-tone-3` / `--rd-tone-4` / `--rd-tone-5` / `--rd-tone-6` | `#1d4ed8` / `#0f766e` / `#6d28d9` / `#be185d` / `#0e7490` / `#475569` | `#60a5fa` / `#2dd4bf` / `#a78bfa` / `#f472b6` / `#22d3ee` / `#94a3b8` | company identity only (letter marks, ranking bars); never green / red / orange, which mean status. ≥ 4.5:1 on `--rd-surface`. Picked with `rd_tone` (company id cycles) |
| `--rd-logo-bg` | `#ffffff` | `#f5f5f2` | light tile behind company logos in both themes |
| `--rd-accent-hover` / `--rd-accent-muted` | `#b00000` / `var(--rd-accent-soft)` | `#ff6b6b` / `var(--rd-accent-soft)` | legacy accent names, kept so existing CSS keeps working |
| `--rd-accent-2` / `--rd-accent-2-strong` / `--rd-accent-2-strong-rgb` / `--rd-accent-2-ink` / `--rd-on-accent-strong` | `#d00000` / `#9b0c14` / `155, 12, 20` / `#7a090f` / `#fff` | `#ff3b3b` / `#ff9a9a` / `255, 154, 154` / `#ffb4b4` / `#0a0a0a` | legacy accent names: the strong variants are text-safe logo red (fill behind white text, or small text on a red tint) |
| `--rd-success-soft` / `--rd-warning-soft` / `--rd-danger-soft` | see rows above | see rows above | tinted backgrounds for status chips and alerts |
| `--rd-radius-xs … -xl` | `0.375` … `1.375rem` | same | corners |

Bootstrap's `--bs-primary/-success/-warning/-danger/-info`, link and subtle-background variables are remapped to these tokens in both blocks, so Bootstrap utilities (`text-success`, `border-danger`, …) already render in Pearl. Need a new colour? Add a token to *both* theme blocks first.

Colour is never the only signal: badges carry a leading dot and a label, trends carry an arrow.

## Typography

Self-hosted, Arabic + Latin subsets, `font-display: swap`. Every full-page template that renders `<head>` includes `{% include "partials/fonts_head.html" %}` (the two shells, login, forbidden do already; new standalone pages must too).

- **Body (Arabic):** IBM Plex Sans Arabic 400/500/600/700 — `--rd-font-ar`.
- **Headings (Arabic):** Noto Kufi Arabic 600/700 — `--rd-font-heading` (applied to `h1–h4`, `.rd-heading-*`, `.rd-card-header`).
- **Numbers and English body:** Inter Tight 500/600/700 — `--rd-font-num` / `--rd-font`.
- Numbers, money, dates, references: `.tabular-nums` (Inter Tight + tabular figures), even in Arabic text.
- Classes: `.rd-heading-xl` (page H1), `.rd-heading-lg` (section), `.rd-text-muted` (secondary).
- `html { font-size: 80% }` is intentional; all `rem` values are relative to it. Don't change it.
- Never link Google Fonts (`test_no_google_fonts`).

## Layout

- Every management page starts with `.rd-page-header` → `.rd-heading-xl` → optional `.rd-text-muted` subtitle → actions in `d-flex flex-wrap gap-2`.
- The management topbar carries the greeting (`.rd-hello`: name + date) and the sidebar is an accordion (`rd-nav-group-toggle` / `rd-nav-link`). Don't re-implement either per page.
- Content uses the Bootstrap grid with `g-3` as the default gap.
- No inline widths/styles; if a max-width is unavoidable, use a small wrapper class and say why.
- No new page shell unless there is a distinct user mode (like employee entry).

## Cards And KPIs

- Surfaces: `.rd-card` + `.rd-card-body` / `.rd-card-header` / `.rd-card-footer`. Don't use raw `.card` / `.app-card` in new templates.
- `.rd-stat` is the KPI card; `.rd-stat--hero` is the one emphasised KPI of a screen. On the management dashboard it follows the theme like the other cards (light surface in light, dark in dark). Today's sales uses identity blue (`--rd-tone-1`) for the icon chip and the sparkline. Only the employee balance hero (`.rd-balance-stats .rd-stat--hero`) is still an inverted ink/pearl card, and its icon chip stays logo red. Value in `.rd-stat-value`; icon tile `.rd-stat-icon` (`--success|warning|danger|info|muted` tints).
- Deltas use `.rd-trend--up` (success) / `.rd-trend--down` (warning, **not** danger or orange-fill) / `.rd-trend--flat` (muted on `--rd-surface-2`); it is LTR-isolated. The chip looks the same on the hero card (tinted fill, no special case).
- `.rd-spark` is the 2 px non-scaling KPI sparkline, full card width and 1.75rem tall on its own row under the value (ink stroke, mirrored in RTL); it is not rendered for an all-zero or < 2-point series; `.rd-spark--accent` strokes it with `--rd-accent`, and on the dashboard sales card that stroke is `--rd-tone-1`.
- Partials (all in `templates/partials/`):
  - `kpi_trend.html` — arg `trend` (from `{% rd_trend series as trend %}`: `direction` up|down|flat, `pct`). Renders the chip; the "vs yesterday" text is visually hidden (no `title`).
  - `kpi_sparkline.html` — args `points` (from `{% rd_sparkline_points series "count" as points %}`), optional `accent=True` for `.rd-spark--accent`. Decorative (`aria-hidden`); outputs nothing when `points` is empty (the tag returns `""` for all-zero series).
  - `empty_state.html` — args `title` (required, already translated), optional `text`, `icon` (`chart` | `building` | `trophy` | `list`; any other non-empty value shows `list`), `action_url` + `action_label`. Static content: it carries no live-region role. Look: light, never a grey well — transparent, compact (1rem) padding, small `--rd-surface-2` icon circle, muted 0.9rem title; standalone = dashed hairline, inside `.rd-card` / `.rd-card-body` = no box at all.
  - `company_mark.html` — args `company` (Company or None), `name`, `cls` (caller's size class, e.g. `rd-supplier-avatar`, `rd-top-avatar`). Shows the company logo on a `--rd-logo-bg` tile (`.rd-company-mark.has-logo`), else the first letter in the company tone (`.rd-tone-N`, from `company.pk|default:name|rd_tone`). Decorative: the name is always printed beside it. Put the same `.rd-tone-N` on a row to colour its ranking bar (`.rd-top-list-fill`).
- Phone-status donut legend (`.rd-phone-lifecycle`): each count takes its swatch's hue (active = `--rd-success`, ends today = `--rd-tone-5`, disconnected only = `--rd-muted`, left the agency = `--rd-accent`, the last one a link with a dotted resting underline). The line-statement chips (`.rd-phone-status`) use the same variables. Colours are contrast-tested in both themes.
- Supplier balances keep semantic colour: amount `kpi-danger` / `kpi-warning` / `kpi-success` (negative / zero / positive), the status line takes the same hue.
- Loading placeholders: `.rd-skeleton` (sweep on a pseudo-element, transform only).
- Avoid large empty cards; merge related small metrics.

## Top Bar And Menus

- The management top bar (`.rd-topbar`, sticky) holds the greeting, search, bell, theme button, language button, sales-entry CTA and avatar menu. Language is ONE button (`partials/lang_toggle.html`, `.rd-theme-btn.rd-lang-btn`, same `set_language` form with `hx-boost="false"`): it looks like the theme button and shows the language you switch TO (`EN` in Arabic, `AR` in English). The employee top bar carries the same two buttons (`.rd-employee-topbar__tools`); the login page has them in `.rd-login-controls`. The avatar menu only holds identity, Settings and Log out. Touch targets ≥ 3.45rem below 992 px.
- Popovers (search results, bell, user menu) use `--rd-surface-raised` + `--rd-shadow-pop`. Below 576 px they become sheets: `position: absolute` anchored to the sticky bar (their wrappers are `position: static` there), 0.5rem side insets, `dvh` max-height with a bottom gap, one scroll box. Never depend on `backdrop-filter` for a containing block.

## Buttons

- Primary: `btn btn-primary` (ink). One per action group.
- The single special action of a screen: `btn btn-accent` (logo red, text `--rd-on-accent`). At most one per screen.
- Secondary/export/edit: `btn btn-outline-secondary`. Permanent delete: `btn btn-outline-danger`. Reversible cancel/void is not red.
- Table actions are `btn-sm`. Avoid `btn-link` for operational actions.
- Toggles are Bootstrap `.form-check.form-switch` (Pearl skin in `design-system.css`); don't hand-roll switches. Period/segment pickers: `.rd-period-switch`.

## Badges And Status

Use `rd-badge` + one variant; the leading dot is automatic (skipped on `.tabular-nums` badges and badges that contain an icon).

- `.rd-badge--paid` — success / active / paid.
- `.rd-badge--pending` (alias `.rd-badge--warning`) — waiting / needs attention.
- `.rd-badge--conflict` — data conflicts that need review.
- `.rd-badge--cancelled` — failed / cancelled / destructive.
- `.rd-badge--neutral` — inactive, metadata, plain labels.
- Sale chips: loss stays `.rd-badge--cancelled`, payment to an employee is `.rd-sale-employee-payment-badge` (`--rd-tone-2` teal), New SIM is `.rd-sale-new-sim-badge` (warning yellow), on account is `.rd-sale-on-account-badge` (`--rd-tone-3`), eSIM is `.rd-sale-esim-badge` (`--rd-tone-1`). The same classes are used wherever those facts are shown.

Never `badge bg-*` / `badge text-bg-*` (`test_no_bootstrap_color_badges`). Errors are `--rd-danger`, not the logo-red accent.

## Filters, Data Grids, Sorting

Unchanged from the structural contract; only the look is Pearl.

- Filters sit above data: `filter_card_open.html` → `form.row.g-3.align-items-end` → `filter_card_close.html`. Hidden `sort`/`order` for sortable data. Controls `form-control-sm` / `form-select-sm`. Reset = `btn btn-outline-secondary btn-sm`.
- Data grid skeleton:

```html
<div class="rd-card overflow-hidden rd-datagrid" id="...">
  <div class="rd-datagrid-toolbar rd-card-header py-2 d-flex flex-wrap align-items-center justify-content-between gap-2">
    <span>Title</span>
  </div>
  <div class="rd-table-dual rd-datagrid-body">
    <div class="d-none d-md-block rd-data-shell">
      <div class="table-responsive border-0">
        <table class="table mb-0 align-middle rd-table-modern">...</table>
      </div>
    </div>
    <div class="d-md-none rd-mobile-stack p-2">...</div>
  </div>
  {% include "partials/pagination.html" with page_obj=page_obj %}
</div>
```

- Desktop tables: `.rd-table-modern` only. It styles the `<thead>`; **never add `table-light`**.
- Empty rows: `<div class="empty-state border-0 mb-0">…`.
- Sortable headers: `.rd-datagrid-sort`; active header owns `is-active is-asc|is-desc`, rendered by the server. No stale `hx-vals` for sort; reset page to `1` on sort change.
- Row selection (any list with checkboxes and a bulk action) uses `.rd-select` and `static/js/rd-select.js`. The header checkbox selects the current page only. The action bar is `.rd-select-slot`: it covers the column titles and stays out of flow, so the table does not grow, shrink, or shift. When every row on the page is checked and `data-match-count` is larger, the bar offers “Select all N results”; clearing any row leaves that mode. On narrow screens the same bar is fixed to the bottom. Status text is `partials/rd_select_status.html`; the mobile page checkbox is `partials/rd_select_page.html`. Do not put a second toolbar above the table.

## Forms And Tabs

- Simple forms: `d-grid gap-2`; filter/search bars: `row g-3 align-items-end`. Labels stay visible except in dense toolbars. Short help text. `novalidate` where server errors are expected.
- Form-wide errors become toasts (`partials/form_errors_toast.html`); field errors stay under their fields.
- Tabs: `.rd-section-tabs` on Bootstrap `nav nav-tabs`, for major sections only — not for filter changes.

## HTMX And Feedback

- HTMX for list filtering, sorting, pagination and small in-place actions. `hx-target` is the data-grid container, not an inner table (unless replacing a single row).
- After actions that change summary cards or balances, redirect or refresh the whole section so numbers stay in sync.
- Irreversible actions: `hx-confirm` or `onsubmit="return confirm(...)"`. Both open the in-app confirm (`.rd-confirm` / `window.rdConfirm`), not the browser dialog. `alert(...)` is a toast.
- Feedback goes through Django messages → corner toasts. Never loop over `messages`, never `alert-success`/`alert-danger` boxes; JS uses `window.rdToast` / `window.rdNavigate`. Full rules: `.cursor/rules/ui-navigation-and-toasts.mdc`.
- Navigation feedback is built in: a top progress bar and a dim on `[data-rd-live].is-live-loading` (`static/js/nav.js`). Mark live-swapped regions with `data-rd-live` instead of writing spinners.

## Motion

- 150–450 ms, `transform` and `opacity` only (no animating width/height/top/left/box-shadow layout). Use `--rd-transition-fast` / `--rd-transition`.
- Purposeful only: state change, progress, loading. No decorative loops other than `.rd-skeleton`.
- `prefers-reduced-motion` already neutralises animations globally in `design-system.css`; don't add per-component opt-outs that fight it.

## Mobile

- Every important desktop table needs a mobile card (`.rd-mobile-row-card`): identifier, status badge, key amount, primary actions. No horizontal-scroll tables on phones.
- **Employee entry is one screen**: the common task (record a sale) fits without scrolling on a phone; secondary fields collapse.
- Touch targets ≥ 44 px (≈ 2.75 rem at the 80% root) with ≥ 8 px between neighbours.
- Employee shell uses a bottom tab bar (`.rd-mobile-tabbar`) with an ink FAB for the primary "sales" action. Don't add a second fixed bottom element.
- Form controls stay ≥ 16 px on small screens (iOS auto-zoom guard in `design-system.css`).

## Accessibility

- Body text ≥ 4.5:1; meaningful non-text (borders of inputs, icons, focus) ≥ 3:1. Use `--rd-accent-ink` for accent text, `--rd-muted` (not `--rd-faint`) for readable secondary text.
- Visible keyboard focus everywhere: `:focus-visible` gets a 2 px `--rd-ink` outline (grouped rule at the end of `design-system.css`). New interactive components with custom chrome must be added to that rule.
- Never rely on colour alone; keep labels and icons with status.

## Dark Mode

- Set by `data-bs-theme="dark"` on `<html>`; tokens swap, components don't.
- Only token-based colours work in both themes — that is why raw hex is forbidden. Surfaces are layered (`bg-body` < `--rd-surface` < `--rd-surface-2`); shadows give way to inset hairlines.
- Check a new page in both themes before merging.

## Copy And Language

- UI strings via `{% trans %}`; add Arabic to `locale/ar/LC_MESSAGES/django.po` and run `compilemessages` (the `.mo` is committed).
- Arabic labels are clear and operational. Short titles, explanatory subtitles.
- Don't mix hard-coded Arabic with translated strings unless the section is intentionally Arabic-only.

## Do Not

- No raw hex colours in templates or in `app.css` / `number-check.css` / `login.css` / below `@end-tokens` — use tokens.
- No Bootstrap colour badges (`badge bg-*`, `badge text-bg-*`) — use `rd-badge--*`.
- No `table-light`; `rd-table-modern` styles the header.
- No Google Fonts or other CDN fonts — `fonts_head.html` only.
- No inline styles for repeated visual patterns — add or reuse a class.
- No accent red for errors, destructive actions or warnings; no accent primary buttons; no more than one `btn-accent` per screen.
- No new visual style for a single page when an `rd-*` component fits; no raw `.card`, `.table-sm`; no filters inside table headers; no whitespace "balancing".
- No `alert-success` / `alert-danger` boxes or `messages` loops; no new `<body>`.

## Page kit

Shared building blocks for admin pages: template tags in `core/templatetags/kit.py` (`{% load kit %}`), partials in `templates/partials/kit/`, styles in the `Page kit` section at the end of `design-system.css`. It adds no tokens.

### Forms

- Render a whole form with `{% rd_fields form %}` (options: `size="sm"`, `full="name notes"` for fields that span both columns, `exclude="is_active"` for fields placed elsewhere). It prints hidden fields, shows form-wide errors as a toast, then a two-column `.rd-form-grid` (one column below 768px).
- Render a single field with `{% rd_field form.name %}` (options: `size`, `full`, `label`, `help`). It supplies the label (no colon, `*` on required fields), the Bootstrap control classes, `is-invalid`, and the `aria-describedby` link to help and error text. `RadioSelect` / `CheckboxSelectMultiple` render as a vertical `.rd-field__choices` list of `form-check-input` rows (≥ 44px below 992px), never `form-control`.
- Never write `label_tag`, `{{ field }}` or `field.errors` by hand for a kit page, and never put `form-control` classes on a widget to style it.
- File inputs always go through the kit: it renders the Arabic drop zone (`.rd-file-pick`) plus, for an optional field with a stored file, the current file name and a "Remove current file" checkbox. Do not use Django's default `ClearableFileInput` markup.

- Form footer: put Save (`btn-primary`) and Cancel (`btn-outline-secondary`) in `<div class="rd-form-actions">` at the end of the `<form>`. It is a wrapping flex row with a top border, aligned to the inline start.
- Search box (`.rd-filter-search`): an `a.rd-filter-search__clear` after the input is shown only while the input has text (`:placeholder-shown`), so it works with live search. Touch size 3.45rem below 992px.
- Employee-shell pages keep the topbar `<h1>` as their title; a template shared by both shells (account settings) renders `rd_page_header` only under the management shell.

### Header and width

- Every management page starts with `{% rd_page_header title subtitle="" back_url="" back_label="" meta="" %}…actions…{% end_rd_page_header %}` inside one `.rd-page` wrapper. The body is the actions slot (buttons, links) and may be empty. `back_url` adds a "Back" link (label overridable with `back_label`); its arrow flips under `[dir="rtl"]`. `meta` is a small muted figure such as a count. Below 576px the actions take the full row and stretch.
- Employee-shell pages that already show the title in the topbar use `{% rd_page_header title shell_title=True %}{% end_rd_page_header %}`: the tag still satisfies the page-kit convention test, but it renders nothing and discards any actions body so there is only one `<h1>`.
- Width: plain `.rd-page` is the full content width (lists, reports, detail pages); `.rd-page--form` caps at 56rem for forms; `.rd-page--settings` caps at 76rem for settings and branding. Both capped variants are centred.
- `.rd-admin-page` and `.rd-admin-page--wide` (in `app.css`) stay for other areas; admin pages do not use them any more.

### Filters, tables, row actions

- Filters: `{% rd_filter_card form action="" reset_url="" hx_target="" indicator_id="" keep="sort order" title="" open=None hx_vals="" show_errors=True %}` draws the collapsible card and a GET form (`.rd-filter-grid`, fields at `size="sm"`; a first field named `q`, `search` or ending in `_q` is `.rd-field--wide`). The badge counts only the form's own fields (prefix aware) that carry a non-blank value in `request.GET`; `tab`, `sort`, `page` never count. `keep` re-posts the listed GET keys as hidden inputs. `hx-get`/`hx-target`/`hx-push-url`/`hx-indicator` appear only when `hx_target` is given; `hx_vals` (a JSON string such as `'{"partial": "ledger"}'`) adds `hx-vals` to that form only and only with `hx_target` (never wrap the card in an element with `hx-vals`: htmx merges ancestor values into the Reset link too); without it the form is a plain GET to `action` (default: the current path). `open=None` opens the card when a filter is active or the form has errors. For a page whose results are swapped by htmx, pass the card an unbound copy (`Form(initial=request.GET.dict())`) and render field and form-wide errors inside the results partial (`rd-field__error rd-grid-error`), so they never go stale. Form-wide errors (e.g. a reversed date range) show under the grid; `show_errors=False` leaves them out of the card for pages that render them elsewhere (e.g. inside an htmx-swapped results grid, so they never go stale). Apply is `btn-primary btn-sm`; Reset is an outline link to `reset_url` with `hx-boost="false"` (a full navigation, so no inherited `hx-vals`/htmx attribute reaches it). Actions sit in the last grid column.
- Sort headers: `{% rd_sort_link "role" _("Role") sort order %}` (options `page_key`, `sort_param`, `order_param`, `first`). Active column in desc goes to asc, the active asc column goes to desc, any other column goes to `first` (default `"desc"`, same as the hand-written headers it replaces). The page key is reset to 1. Output: `a.rd-datagrid-sort[.is-active.is-asc|.is-desc]`.
- Row actions: `{% include "partials/kit/row_actions.html" with edit_url=… delete_url=… confirm=_("…") next=… %}`. Icon variant (default) gives `.rd-row-actions` with `.rd-icon-btn` buttons carrying `aria-label` and `title`; `variant="text"` gives labelled outline buttons for mobile cards. `edit_label` / `delete_label` override "Edit" / "Delete". Add `hx_target` (and `hx_swap`) to delete through htmx instead of a boosted POST. An action without its URL is left out. `.rd-icon-btn` is 2.4rem square, 3.45rem below 992px.
- Empty tables: `{% include "partials/kit/empty_row.html" with colspan=5 title=_("No users.") text=… icon="list" %}` inside `{% empty %}`.
- Pagination: the "Rows" links reset whichever `page_key` the pager uses.

### Reconcile page

Layan and Sky matching share `companies/partials/reconcile_results.html` + `reconcile_rows.html` (`.rd-rec-*`, `static/js/reconcile.js`). Order: run card, four quiet `rd-stat`s, number search, then the two action lists ("At supplier, not in system" and "In system, not at supplier", equal weight), then tabs for cost differences, settlements and matched (matched hidden by default). Row actions: **Record sale** for supplier-only numbers (opens sale entry with the phone prefilled and a "don't charge again" note); **Find sale** for system-only numbers and for numbers already logged under another supplier (recording again would double-deduct — open the existing sale and fix the supplier). Rows are one list that stacks as cards in a narrow container; row accents are token borders, never table tints, and a gap always carries a word ("Supplier higher" / "System higher"). The running state is pure CSS on `form[data-rd-submitting]` (set by `nav.js` on boosted submits). Searching shows every panel narrowed to matching numbers and restores the previous tab when cleared.

## Guardrails And Legacy Lists

`core/tests_ui_conventions.py` fails on raw hex, Bootstrap colour badges and `table-light` in any template. The shrink-only sets `LEGACY_HEX`, `LEGACY_BADGES`, `LEGACY_TABLE_LIGHT` (and the page-kit sets `LEGACY_PAGE_HEAD`, `LEGACY_APP_CARD`, `LEGACY_LABEL_TAG`, `LEGACY_INLINE_WIDTH`) are empty after the Pearl kit migration — keep them empty. **Never add a file to them**; fix the template instead (`test_legacy_lists_only_shrink` fails otherwise). The public phone-refresh page uses self-hosted fonts via `partials/fonts_head.html` and its own `refresh.css` (standalone; not the management shell).
