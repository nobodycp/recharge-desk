# Pearl Phase 2 — Management shell + dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the management shell (sidebar, top bar, search, notifications, user menu) and the dashboard in Pearl, fix the shell's mobile bugs, and close the accessibility items deferred from Phases 0–1, without changing any logic.

**Architecture:** The shell structure from Phase 0 stays; this phase tunes its CSS, moves language + theme into the user menu (same as the employee shell), and fixes panel positioning. The dashboard gets the spec's KPI variant (trend chip + SVG sparkline) built by template tags from data already in the context, an empty state, and localised chart labels. One new token (`--rd-field-border`) lifts every form field to 3:1. `page-scope.js` learns to remove `htmx.onLoad` callbacks registered by page scripts.

**Tech Stack:** Django 4.2 templates + template tags, Bootstrap 5 (vendored), htmx 1.9.12, vanilla JS, plain CSS. Node for `tests/js/*.test.js`.

**Spec:** `docs/superpowers/specs/2026-10-14-pearl-redesign-design.md` (§3 tokens, §5 components incl. "KPI card variant with trend chip and optional sparkline (pure SVG/CSS, server data only)" and `partials/empty_state.html`, §6 management shell, §10 Phase 2). Visual reference: `.superpowers/brainstorm/50122-1791992216/content/visual-direction-v2.html`, option C (lines 195–230 CSS, 396–474 markup; gitignored, read locally). Research notes: `.superpowers/phase2-inventory.md` (file:line map of the shell, dashboard, hooks and deferred items).

## Global Constraints

- No changes to models, forms, URLs, permissions, querysets, cached KPI payloads or htmx endpoints. The dashboard view may not gain queries; new display values are derived in templates/template tags from context that already exists (`chart_series`, `period`, KPI scalars).
- Keep every JS hook: everything listed in `.superpowers/phase2-inventory.md` §1 "JS reading shell hooks" (`data-rd-nav*`, `data-rd-search*`, `#rd-global-search`, `data-rd-notif*`, `data-rd-nav-count`, `data-app-theme-toggle`, `.rd-period-switch__opt`, `#rd-dashboard`, `#connection-health-alert`, `rd-phone-donut`, `rd-hello*`, `rd-user-menu-trigger`, `set_language` form).
- Same sidebar links, labels, order and permission gates. Displayed information must not change (no hidden counts, no removed links); the user's uploaded logo stays as is.
- Tokens only (no raw hex outside the token blocks); ink primary; orange accent only (dots, progress, the hero icon, sparkline on the hero); errors `--rd-danger`; status never colour-only.
- Touch targets ≥ 44 px (3.45rem) at < 992 px; desktop icon buttons ≥ 2.6rem. Motion 150–450 ms, transform/opacity only, `prefers-reduced-motion` respected.
- One `<h1>` per management page = the page's own content heading (the top-bar greeting stays a non-heading).
- RTL first; every new string via `{% trans %}` / `pgettext` with Arabic in `locale/ar/LC_MESSAGES/django.po`, `.mo` recompiled and committed.
- Query budgets in `core/tests_query_budget.py` must not be loosened (dashboard 7 warm / 35 cold).
- Rules: `.cursor/rules/pearl-ui.mdc`, `.cursor/rules/ui-navigation-and-toasts.mdc`, `DESIGN.md`. Shrink-only `LEGACY_*` sets.
- Tests: `.venv/bin/python manage.py test`; JS: `node tests/js/<file>.test.js`. `sales.tests_number_check.HappyPathTests` fails on 2026-10-15 only (pre-existing date flake).

## Rulings made while planning (owner said: proceed, review at the end)

- R1 Language select (`#rd-lang-mgmt`, same form) and theme toggle move from the management top bar into the user-menu panel (`user_menu.html` `prefs=True`), exactly like the employee shell. The top bar keeps greeting · search · bell · "Sales entry" · avatar (mockup C). `core/tests_shell.py::test_topbar_keeps_controls_and_hooks` is updated to assert the controls are inside the user menu — cost if wrong: one template block moves back.
- R2 Sidebar count chips become ink pills (mockup), not red; the count and its `hidden` toggling stay — cost if wrong: one CSS value.
- R3 KPI trend chip only where a true comparison exists without new queries: the hero "sales" card in the **day** period (today vs yesterday from `chart_series[-1]` / `[-2]`, by `count`). Other cards/periods show no chip. Sparkline on the hero card for every period from `chart_series` counts (the same buckets the chart below shows) — cost if wrong: remove the chip/sparkline include.
- R4 Mockup pieces that need data not in context are out of scope: recent-operations list, sidebar "SMS gateway connected" card, profit/debt/loss sparklines, the line chart's comparison series. The bar chart stays DOM bars (strict CSP, no chart library) and is re-skinned.
- R5 Chart weekday labels are rendered per language in the template (`rd_weekday_initial` filter on `d.date`); the cached `tick` stays untouched (pinned by `reports/tests.py` and language-neutral cache).
- R6 Duplicate sidebar labels ("الإعدادات", "التقارير" under different groups) keep their text; each gets an `aria-label` "<group> — <label>" so screen readers can tell them apart.

## Review Focus

1. **Notification and search panels at 375 / 430 px, RTL and LTR** — panels must stay fully inside the viewport (bell panel currently ends at x≈490 in a 375 px viewport). Pinned in Task 3 browser step + `test_panels_use_viewport_safe_positioning`.
2. **Zero-data dashboard** (no sales today) — chart shows the empty state instead of 3 px stubs; sparkline and trend chip render nothing broken (no `NaN`, no division by zero when yesterday = 0). Pinned in Task 4 (`test_trend_handles_zero_previous`, `test_sparkline_points_all_zero`) and Task 5 (`test_chart_empty_state_when_no_volume`).
3. **English UI** — chart labels English initials, `dir` correct, trend chip sign isolated LTR. Pinned in Task 5 (`test_weekday_initial_english_and_arabic`) and Task 4 (`test_trend_chip_is_ltr_isolated`).
4. **Mobile drawer** — opens/closes (button, backdrop, Esc), closed drawer links are not tabbable, rows ≥ 44 px. Pinned in Task 2 (`test_closed_drawer_is_inert` JS test) and the Task 8 browser pass.
5. **Shared partials used by many pages** (`user_menu.html`, `empty_state.html`, field-border token) — management pages other than the dashboard render unchanged apart from the intended look. Pinned in Task 1 / Task 3 tests and the Task 8 page sweep.

---

### Task 0: Baseline

- [ ] **Step 1:** Branch `pearl-phase2` from `main` (done). Probe server as in Phase 1 (`.superpowers/probe.sqlite3`, rebuilt from `db.sqlite3`; backup at `.superpowers/probe.backup.sqlite3`; users `admin` / `ياسمين`, password `pearl-probe-1`).
- [ ] **Step 2:** Fill "Baseline" at the end of this plan from the controller's browser pass; commit `docs: Pearl phase 2 baseline`.

### Task 1: Field border token + focus clipping + login polish

**Files:**
- Modify: `static/css/design-system.css` (both token blocks; `.form-control,.form-select` ~2254; `.form-check-input` ~2282; focus rules ~5795), `static/css/employee.css` (`.rd-entry-field`, `.rd-pay-sub-notes__body .form-control`, `.rd-entry-notes__body .form-control`, `.rd-recent-filter` controls), `static/css/login.css` (`.rd-login-input`, `.rd-login-icon`, `.rd-login-lang` focus), `static/js/login.js` (autofill sync, live-region timer), `DESIGN.md` (token table)
- Test: `core/tests_design_tokens.py`

**Interfaces:**
- Produces: `--rd-field-border` (light and dark) — all later tasks use it for any field-like border.

- [ ] **Step 1: Failing tests** in `core/tests_design_tokens.py`:
  - `test_field_border_token_defined_in_both_themes`: `--rd-field-border` appears in the light and the dark token block.
  - `test_field_border_contrast`: resolve the token against `--rd-surface` in both themes (reuse the file's existing colour/contrast helpers; if the value is a `color-mix`, compute it) → ratio ≥ 3.0.
  - `test_fields_use_field_border`: the `.form-control, .form-select` rule, `.form-check-input`, `.rd-entry-field` and `.rd-login-input` use `var(--rd-field-border)`.
  - `test_focus_ring_not_clipped_in_scroll_containers`: a rule targets `.table-responsive .btn:focus-visible` (and `.rd-table-wrap`, `.rd-card.overflow-hidden`) with `outline-offset: -2px`.
  - `test_login_icon_uses_muted`: `.rd-login-icon` rest colour is `var(--rd-muted)`.
- [ ] **Step 2:** Run → fail.
- [ ] **Step 3:** Add the token (e.g. `color-mix(in srgb, var(--rd-ink) 40%, var(--rd-surface))`, tuned until ≥ 3:1 in both themes) and apply it. Focus: inset outline inside scroll containers. Login: `.rd-login-icon` → `--rd-muted`; `.rd-login-lang:focus-within` → `:has(select:focus-visible)`; `login.js` replaces the 50 ms live-region timer with `requestAnimationFrame` and runs the filled-state sync once on `animationstart` of the existing `:-webkit-autofill` hook (or once after `load`) — no change to the form, POST, fields or validation.
- [ ] **Step 4:** `.venv/bin/python manage.py test core accounts` → green; commit `feat(pearl): field border token, unclipped focus, login polish`.

### Task 2: Sidebar

**Files:**
- Modify: `templates/partials/management_nav.html`, `static/css/design-system.css` (sidebar ~399–988, count chip ~910), `static/js/nav-toggle.js`, `locale/ar/LC_MESSAGES/django.po`
- Test: `core/tests_shell.py` (class `ManagementSidebarTests`), `tests/js/nav_toggle.test.js` (new, same harness style as `tests/js/employee_check_polling.test.js`)

- [ ] **Step 1: Failing tests:**
  - `test_group_icons_visible`: CSS no longer hides the group icons (`.rd-nav-group-toggle` icon rule has no `display: none`); each group toggle has an icon `svg`.
  - `test_count_chip_is_ink`: `.rd-nav-count` uses `var(--rd-primary-bg)` / `var(--rd-primary-fg)`, not `--rd-danger`.
  - `test_untranslated_labels_use_trans`: `management_nav.html` has no literal `تقارير المزودين` outside `{% trans %}`; "API" is `{% trans "API" %}`.
  - `test_duplicate_labels_have_group_aria_label` (R6).
  - `test_mobile_rows_meet_touch_target`: under `@media (max-width: 991.98px)` nav links have `min-height: 3.45rem`.
  - JS `nav_toggle.test.js`: closed panel gets `inert` (and loses it when open); Esc closes an open panel and returns focus to the toggle; backdrop click closes.
- [ ] **Step 2:** Run → fail.
- [ ] **Step 3:** Pearl sidebar per mockup C: link radius `.85rem`, padding `.6rem .75rem`, gap `.7rem`; group labels ≥ `.82rem` 600 muted with their icon; active = surface pill + hairline + orange icon (keep); count chip = ink pill (R2). `nav-toggle.js`: toggle `inert` on the panel when closed at < 992 px (never at ≥ 992), Esc closes + focus back to `[data-rd-nav-toggle]`. Keep every `data-rd-nav*` hook and link order.
- [ ] **Step 4:** `.venv/bin/python manage.py test core` + `node tests/js/nav_toggle.test.js` → green; commit `feat(pearl): management sidebar`.

### Task 3: Top bar, search, notifications, user menu

**Files:**
- Modify: `templates/base_management.html`, `templates/partials/user_menu.html` (already supports `prefs=True`), `templates/partials/nav_notifications.html`, `static/css/design-system.css` (topbar ~990–1113, search ~1118–1413, notifications ~4245–4350, user menu ~4010–4243)
- Test: `core/tests_shell.py` (update `test_topbar_keeps_controls_and_hooks`; add `ManagementTopbarTests`)

- [ ] **Step 1: Failing tests:**
  - `test_prefs_inside_management_user_menu` (R1): `id="rd-lang-mgmt"` and `data-app-theme-toggle` are inside the `rd-user-menu-panel` of the management top bar and appear nowhere else in the header; the `set_language` form and `next` are unchanged.
  - Updated `test_topbar_keeps_controls_and_hooks`: same hooks, new location.
  - `test_topbar_touch_targets`: under `max-width: 991.98px` `.rd-search-toggle`, `.rd-notif-trigger`, `.rd-user-menu--ava .rd-user-menu-trigger`, `.rd-menu-btn` have `min-width`/`min-height: 3.45rem`; desktop ≥ `2.6rem`.
  - `test_panels_use_viewport_safe_positioning`: at `max-width: 575.98px` `.rd-notif-menu .rd-user-menu-panel` and `.rd-search-panel` are `position: fixed` with `inset-inline: .5rem` (or equivalent clamp) — never wider than the viewport.
  - `test_search_panel_desktop_width`: at ≥ 992 px `.rd-search-panel` has `min-width` ≥ `26rem` and a `max-height` with `overflow-y: auto`.
- [ ] **Step 2:** Run → fail.
- [ ] **Step 3:** Implement. Top bar per mockup C (greeting + date start; search pill · bell · "Sales entry" · avatar end); bell shows the existing count badge; notification rows with count 0 render muted (text unchanged). Mobile (< 576 px): the "Sales entry" button keeps its label visually hidden but accessible, shows a "+" icon, 3.45rem. Search results panel: desktop wide + scrollable, mobile full-width sheet under the top bar.
- [ ] **Step 4:** `.venv/bin/python manage.py test core` → green; commit `feat(pearl): management top bar, search and notification panels`.

### Task 4: Dashboard KPI cards (trend chip + sparkline)

**Files:**
- Create: `templates/partials/kpi_sparkline.html` (inline SVG), `templates/partials/kpi_trend.html`
- Modify: `core/templatetags/ui_tags.py`, `templates/reports/dashboard.html` (KPI row only, :30–93), `static/css/design-system.css` (`.rd-stat` head row, `.rd-trend`, new `.rd-spark`), `static/css/app.css` (KPI row tweaks 201–336 — move what remains into design-system or delete if dead)
- Test: `core/tests_ui_tags.py` (new), `reports/tests.py` (dashboard render)

**Interfaces:**
- Produces in `ui_tags.py`:
  - `rd_sparkline_points(series: list[dict], key: str = "count", width: int = 100, height: int = 28) -> str` — SVG `points` string, x evenly spaced over `width`, y scaled to `height` with 2 px padding; `""` for fewer than 2 points; all-zero series → flat line at the bottom.
  - `rd_trend(series: list[dict], key: str = "count") -> dict | None` — compares last vs previous bucket: `{"direction": "up"|"down"|"flat", "pct": int}`; `None` when fewer than 2 buckets or the previous value is 0.
  - Partials: `{% include "partials/kpi_sparkline.html" with points=… accent=True %}`, `{% include "partials/kpi_trend.html" with trend=… %}` (renders nothing when `trend` is None).

- [ ] **Step 1: Failing tests** `core/tests_ui_tags.py`: `test_sparkline_points_basic` (3 points `[0,5,10]` → first y = 26, last y = 2, x = 0/50/100), `test_sparkline_points_all_zero` (flat, no division error), `test_sparkline_points_too_short` (`""`), `test_trend_up_down_flat`, `test_trend_handles_zero_previous` (`None`), `test_trend_chip_is_ltr_isolated` (rendered partial contains `dir="ltr"` on the number and a text arrow/sign, not colour only). `reports/tests.py`: `test_dashboard_hero_has_sparkline_and_day_trend` (day period: hero card contains `rd-spark` and, with yesterday > 0 in the fixture, `rd-trend`); `test_dashboard_month_has_no_trend` (R3).
- [ ] **Step 2:** Run → fail.
- [ ] **Step 3:** KPI card layout per mockup C: head row = neutral icon chip at the inline start + trend chip at the end; label; ink value (keep `kpi-*` semantic colour where the value's sign is the information, e.g. profit); hero sparkline (orange stroke on ink, 2 px, `aria-hidden`); remove the dead space. Card heights equal per row. Query budget unchanged.
- [ ] **Step 4:** `.venv/bin/python manage.py test core reports core.tests_query_budget` → green; commit `feat(pearl): dashboard KPI cards with trend and sparkline`.

### Task 5: Dashboard chart, lists, empty state

**Files:**
- Create: `templates/partials/empty_state.html` (args: `icon` optional, `title`, `text` optional, `action_url`/`action_label` optional; uses the existing `.empty-state` CSS restyled for Pearl)
- Modify: `templates/reports/dashboard.html` (status row + chart row, :96–271; health alert strings :17, :22 → `{% trans %}`), `core/templatetags/ui_tags.py`, `static/css/design-system.css` (mini chart ~4352–4590, top list ~4591–4730, donut ~5184–5410, `.empty-state` ~2523), `static/css/app.css` (supplier panel 434–575, period switch 340–418 — keep hooks), `locale/ar/LC_MESSAGES/django.po`
- Test: `reports/tests.py`, `core/tests_ui_tags.py`

**Interfaces:**
- Produces: `rd_weekday_initial(iso_date: str) -> str` filter in `ui_tags.py` (localised one-letter weekday via `pgettext("weekday initial", …)` with one context per day so English "T"/"S" duplicates don't collide; Arabic = today's letters ن ث ر خ ج س ح); `partials/empty_state.html` for later phases.

- [ ] **Step 1: Failing tests:** `test_weekday_initial_english_and_arabic` (2026-10-12 = Monday → "M" with `translation.override("en")`, "ن" with `"ar"`); `test_chart_empty_state_when_no_volume` (`chart_total_volume == 0` → `empty-state` rendered, no `.rd-mini-chart-bar-cell`); `test_chart_bars_when_volume` (existing bars + `rd-period-switch__opt` hooks still present); `test_health_alert_strings_translated` (English page shows English text for the two alert strings); `test_empty_state_partial_renders_title_and_action`.
- [ ] **Step 2:** Run → fail.
- [ ] **Step 3:** Chart card per mockup C header (title, big total with `.tabular-nums`, average); bars ink at 85 % with the "today" bar orange, rounded tops, hairline grid; labels via `rd_weekday_initial` when `period == "day"` else existing `d.tick` (R5). Donut legend values ink with a coloured dot (not coloured numbers). Supplier and top-companies lists: Pearl rows, `empty_state` partial for their empty cases. Keep `#rd-dashboard`, `rd-phone-donut`, `#connection-health-alert`, period-switch attributes.
- [ ] **Step 4:** `.venv/bin/python manage.py test reports core` → green; compile messages; commit `feat(pearl): dashboard chart, lists and empty state`.

### Task 6: `page-scope.js` tracks `htmx.onLoad`

**Files:**
- Modify: `static/js/page-scope.js`, `.cursor/rules/ui-navigation-and-toasts.mdc` (one line under "Already automatic")
- Test: `tests/js/page_scope.test.js` (new)

- [ ] **Step 1: Failing JS test:** with a fake `htmx` (`onLoad(fn)` returns a wrapper; `off(evt, wrapper)` records calls) and a fake `document.currentScript` inside `<body>`: a page-owned `htmx.onLoad(fn)` call is removed via `htmx.off("htmx:load", wrapper)` when `htmx:beforeSwap` fires for a body swap; a head-owned call (currentScript in `<head>`) is never removed; calling page-owned `onLoad` twice for the same `fn` across two visits leaves exactly one live registration.
- [ ] **Step 2:** Run `node tests/js/page_scope.test.js` → fail.
- [ ] **Step 3:** In `page-scope.js`, wrap `htmx.onLoad` as soon as `window.htmx` exists (page-scope loads before htmx: patch on the `htmx` script's load or with a `defineProperty` setter on `window.htmx`; pick the simpler that works with `partials/nav_head.html` order), decide ownership synchronously with the existing `pageOwned()`, record the returned wrapper, and in `leavePage()` call `htmx.off("htmx:load", wrapper)`. No change for head scripts.
- [ ] **Step 4:** JS test green; `.venv/bin/python manage.py test core` green; commit `fix(pearl): page-scope removes page htmx.onLoad callbacks`.

### Task 7: Phase 1 residuals

**Files:**
- Modify: `static/css/employee.css` (`.rd-entry-subrow` ~1861, pager ~2112–2160), `templates/sales/employee_sale_edit.html` (Back link placement only)
- Test: `sales/tests_employee_entry_ui.py`

- [ ] **Step 1: Failing tests:** `.rd-entry-subrow` has `width: 100%`; sale-edit Back link uses `margin-inline-start: auto` (no physical side); employee pager edges/numbers fit one row at 375 px (`.rd-pager__num` ≤ `2.75rem` wide below 576 px or first/last edges hidden below 576 px — numbers stay visible).
- [ ] **Step 2:** Run → fail. **Step 3:** Implement. **Step 4:** `.venv/bin/python manage.py test sales` → green; commit `fix(employee): Phase 1 layout residuals`.

### Task 8: Phase verification

- [ ] **Step 1:** Full suite green (date flake excepted on 2026-10-15 only) + `node tests/js/*.test.js`.
- [ ] **Step 2:** Probe server, dashboard + 5 other management pages (sales list, customers, a report, system settings, audit) at 1280×800 / 375×667 / 430×932, light + dark, Arabic, plus English spot-check of the dashboard: screenshots; `scrollWidth − innerWidth = 0`; every open panel (search, bell, user menu, drawer) fully inside the viewport; Review Focus 1–5 checked by hand.
- [ ] **Step 3:** Fill "After" below; commit `docs: Pearl phase 2 after-numbers`.

## Baseline / After (filled by Task 0 / Task 8)

| Page | Measure | Before | After |
|---|---|---|---|
| Dashboard 1280×800 | `scrollHeight` / DCL | 852 / 97 ms | 800 (fits the viewport) / 48 ms |
| Dashboard 375×667 | `scrollHeight` / hOverflow | 1575 / 0 (430: 1575 / 0) | 1561 / 0 |
| Bell panel 375 | right edge vs viewport | 490 px in a 375 px viewport (overflows ~115 px) | 369 px (sheet under the bar, ~6 px side gaps) |
| Search panel 1280 | width / clipped | as narrow as the toggle; bottom clipped | 333 px (toggle 269 px); not clipped |
| Top-bar targets 375 | size | 8 controls, 30–32 px | 5 visible controls, all 44 px; AR/EN and search rows ≥ 44 px |
| Mobile drawer | Esc / inert / row height | Esc no-op / not inert / ~33 px | Esc closes / inert when closed / 44 px |
| Chart, no sales | bars | 3 px stubs, Arabic-only letters in English | light empty state, no bars; weekday initials translated |

After screenshots (same folder): 1280 light `14-18-33-520Z`, 1280 dark `14-18-43-409Z`, 375 light `14-18-58-441Z`, 375 dark `14-18-50-697Z`.

Baseline screenshots (`/var/folders/bs/8n9gkjm97sl0g9gdp10k91_w0000gn/T/cursor/screenshots/page-2026-10-15T…`): 1280 light `11-51-26-408Z`, 1280 dark `12-16-53-300Z`, 375 light `12-17-16-942Z`, 375 dark `12-18-43-348Z`.
