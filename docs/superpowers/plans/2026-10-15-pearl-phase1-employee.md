# Pearl Phase 1 — Employee (mobile) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the whole employee area to Pearl and make sale entry fit one 375×667 screen (approved compact mockup), without changing any logic.

**Architecture:** Markup is regrouped in place (every id, `name`, `data-*`, `hx-*` and JS-read class is kept); employee-only CSS moves out of `app.css` into a new `static/css/employee.css` loaded by `base_employee.html`; JS changes are limited to swapping Bootstrap colour classes for an `is-on` state class and one new client-only progress script.

**Tech Stack:** Django 4.2 templates, Bootstrap 5 (vendored, LTR + RTL), htmx 1.9, vanilla JS, plain CSS.

**Spec:** `docs/superpowers/specs/2026-10-14-pearl-redesign-design.md` (§6 employee shell, §7 sale entry, §10 Phase 1). Visual reference: `.superpowers/brainstorm/50122-1791992216/content/entry-pearl-v2.html` (approved; gitignored, read it locally). Design system: `DESIGN.md`.

## Global Constraints

- No changes to views, forms, URLs, models, permissions, context data, htmx endpoints or form field names. Data not already in the template context is out of scope.
- Keep every id, `name`, `data-*`, `hx-*`, inline `onclick` and JS-read class listed in Task 2's hook test. Company tile attribute order `data-company-id="…"` then `data-can-charge="…"` is pinned by `sales/tests_charge_ui.py`.
- Tokens only (`--rd-*` / `--bs-*`), no raw hex outside the token blocks; Pearl components from `DESIGN.md` (`rd-badge--*`, `btn-primary` ink, `btn-accent` orange max one per screen, ink `:focus-visible` outline).
- Never `badge bg-*`, `text-bg-*`, `table-light`, `alert-success/danger`, `btn-outline-info|warning|primary`, `text-info|primary` as colour in Phase 1 templates or JS.
- Touch targets ≥ 44 px; motion 150–450 ms, transform/opacity only.
- RTL first (logical properties); every new string via `{% trans %}` with Arabic in `locale/ar/LC_MESSAGES/django.po` + `compilemessages` (the `.mo` is committed).
- Query budgets in `core/tests_query_budget.py` must not be loosened.
- Rules in `.cursor/rules/ui-navigation-and-toasts.mdc` and `.cursor/rules/pearl-ui.mdc` apply (htmx.onLoad init, `rdToast`, no bind-once guards).
- Tests: `.venv/bin/python manage.py test`. `sales.tests_number_check.HappyPathTests` has a known date flake on 2026-10-15 (also fails on main); ignore it only on that date.

## Rulings made while planning (owner said: proceed, review at the end)

- R1 Employee top bar = mockup: avatar (user menu, `variant="ava"`) · title · wallet chip at the inline end. Language select and theme toggle move **inside** the user-menu panel (same form, same `#rd-lang-emp`, same theme partial) — cost if wrong: move them back, one template block.
- R2 Product keeps its two levels (line → package): line tiles and package chips are two horizontal chip rows; the package chip shows the price it already carries in `data-default-price` — cost if wrong: one more layout pass.
- R3 Payment and company tiles show icon + name (names already in context; `employee-chip-label` test kept).
- R4 Progress hairline, step dots and the green "number looks valid" tick are client-only display derived from existing DOM state and the existing submit regex `^05[0-5]\d{7}$` (after the same normalisation the submit handler does) — no new server data.
- R5 The estimate moves into the price block header (`cost · profit`); the eSIM / new-SIM / loss notes collapse into one muted line under the price row, shown only when one applies. Optional rows (days, SIM serial, stock preview) may push the page past one screen only while they are active.
- R6 No currency symbol is added (the app shows plain `intcomma` amounts today).

## Review Focus

1. **Long names** — Arabic company / payment / package names (e.g. "بنك فلسطين", "Cellcom 400 — رمضان") must ellipsize inside tiles, never widen the grid or push the page sideways. Pinned in Task 3 (`test_entry_css_ellipsizes_tile_names`) and the Task 9 overflow check.
2. **More than 5 payment methods / 4 companies** — rows wrap; the page may grow by one tile row but Save stays visible (dock is sticky). Pinned in Task 9 browser check with the probe DB (9 payment methods + 4 companies).
3. **Server-side validation errors** — re-render must still show `has-error` / `is-invalid` and the error toast in the compact layout. Pinned by existing `test_entry_validation_errors_are_highlighted` plus Task 2 hook test.
4. **Company switch via htmx** — product chips swap, selection + price + estimate + progress reset. Pinned in Task 4 (`afterSwap` path recomputes progress) and Task 9 manual check.
5. **Dark mode + on-account / to-employee states** — toggled pills use `is-on` with visible state in both themes and keep `aria-pressed`. Pinned in Task 2 (`test_entry_js_uses_is_on_not_bootstrap_colours`).

---

### Task 0: Baseline

- [ ] **Step 1:** Create branch `pearl-phase1` from `main`. Probe server: `.superpowers/probe_settings.py` (DB copy `.superpowers/probe.sqlite3`, already migrated) — `PYTHONPATH=.superpowers DJANGO_SETTINGS_MODULE=probe_settings .venv/bin/python manage.py runserver 127.0.0.1:8765`; users `admin` / `ياسمين`, password `pearl-probe-1`.
- [ ] **Step 2:** At 375×667 on `/ar/employee/sales/` record `document.documentElement.scrollHeight`, the visible height above the tab bar, and screenshots (light + dark) of every Phase 1 page. Write the numbers into "Baseline" at the end of this plan; commit `docs: Pearl phase 1 baseline`.

### Task 1: Employee shell

**Files:**
- Modify: `templates/base_employee.html`, `templates/partials/user_menu.html`, `templates/partials/_employee_bottom_nav_inner.html`
- Create: `static/css/employee.css` (loaded after `app.css` in `base_employee.html`)
- Modify: `core/tests_ui_conventions.py` (add `static/css/employee.css` to the page-CSS no-hex list), `core/tests_shell.py`

**Interfaces:**
- Produces: `static/css/employee.css` (all later tasks put employee CSS here); `{% block employee_progress %}{% endblock %}` directly under the top bar row in `base_employee.html` (Task 4 fills it); `user_menu.html` accepts `prefs=True` to render language + theme rows inside the panel.

- [ ] **Step 1: Failing tests** in `core/tests_shell.py`, class `EmployeeShellTests` (employee user with profile + acting employee, as `accounts/tests.py` builds them):
  - `test_topbar_order`: in the `<header class="rd-employee-topbar"` html, `rd-user-menu` appears before `rd-employee-title`, which appears before `rd-employee-wallet`.
  - `test_prefs_inside_user_menu`: `id="rd-lang-emp"` and the theme toggle markup appear **inside** the `rd-user-menu-panel` element and not elsewhere in the header.
  - `test_employee_css_loaded`: page contains `css/employee.css`.
  - `test_number_check_label_translated`: tab label "فحص الأرقام" still renders in Arabic and the English page renders "Check numbers" (wrap the hard-coded label in `{% trans "Check numbers" %}`, Arabic msgstr "فحص الأرقام").
- [ ] **Step 2:** Run `.venv/bin/python manage.py test core.tests_shell` → the four new tests fail.
- [ ] **Step 3:** Implement. Top bar row: user menu (`variant="ava" prefs=True`), title block, wallet chip (`margin-inline-start: auto`), height ≤ 3.4rem. Wallet = success-soft / danger-soft pill (keep `is-pos` / `is-neg`). Tab bar: keep structure and the raised ink FAB with the orange dot; slim to the mockup proportions; labels `--rd-faint`→`--rd-muted` for inactive (text contrast). Move every `.rd-employee-*` and `.rd-mobile-tabbar*` rule that Phase 1 changes into `employee.css` (delete the moved rules from `design-system.css`; leave untouched rules where they are).
- [ ] **Step 4:** `.venv/bin/python manage.py test core accounts sales.tests employees` → green (existing `test_view_all_link_appears_on_entry_page`, `test_bottom_nav_label_is_number_check` included).
- [ ] **Step 5:** Commit `feat(employee): Pearl employee shell`.

### Task 2: Sale entry markup (one-screen structure)

**Files:**
- Modify: `templates/sales/employee_entry.html`, `templates/sales/partials/employee_product_tiles.html`, `templates/sales/partials/employee_icon_mark.html`
- Test: `sales/tests_employee_entry_ui.py` (new)

**Interfaces:**
- Consumes: Task 1 shell.
- Produces: block wrappers `.rd-entry-blk` with header row `.rd-entry-blk__head` (title + `.rd-entry-blk__end`); in-input label field `.rd-entry-field` (`label.rd-entry-field__label` + the input + optional trailing button); tile grids `.rd-entry-tiles--pay` (5 cols), `.rd-entry-tiles--co` (4 cols, horizontal tile); chip rows `.rd-entry-chips`; price row `.rd-entry-price-row`; estimate `#rd-sale-preview` as `.rd-entry-est` inside the price header; notes line `.rd-entry-notes-line`; dock `.rd-entry-dock` wrapping the existing `.sticky-save`. Toggle state class `is-on` on `#rd-on-account-toggle` / `#rd-employee-payment-toggle`; days state class `has-days` on `#rd-sell-price-wrap`'s row.

- [ ] **Step 1: Failing tests** in `sales/tests_employee_entry_ui.py`, class `EmployeeEntryMarkupTests` (fixture like `sales/tests.py::EmployeeRecentSalesTests`: employee, 2 companies with products, 2 payment methods, `sales_inventory_enabled=True`):
  - `test_all_js_hooks_present`: response contains every one of: `id="employee-sale-form"`, `data-esim-extra=`, `data-sim-preview-url=`, `data-msg-phone-prefix=`, `name="form_token"`, `id="id_reference_number"`, `id="rd-ref-error"`, `id="rd-payer-assist-root"`, `data-api-payer-by-number=`, `data-api-payer-name-suggestions=`, `id="rd-payer-prefill-hint"`, `id="rd-payer-ac-list"`, `class="rd-payer-ac-anchor"`, `id="rd-payment-section"`, `id="rd-on-account-toggle"`, `id="payment-tiles"`, `data-payment-id=`, `id="id_payment_method"`, `id="id_on_account"`, `id="id_paid_via_employee"`, `id="product-tiles-loading"`, `id="company-tiles"`, `hx-target="#product-tiles-container"`, `hx-indicator="#product-tiles-loading"`, `id="id_company"`, `id="id_product"`, `id="product-tiles-container"`, `id="rd-duration-days-wrap"`, `id="rd-sell-price-wrap"`, `id="rd-days-hint"`, `id="id_is_esim"`, `id="id_is_new_sim"`, `id="rd-new-sim-extra-label"`, `id="rd-sim-serial-wrap"`, `id="rd-sim-stock-preview"`, `id="rd-sale-preview"`, `id="rd-preview-cost"`, `id="rd-preview-profit"`, `id="rd-preview-esim-note"`, `id="rd-preview-new-sim-note"`, `id="rd-preview-loss-note"`, `id="id_notes"`, `value="save"`, `class="employee-clipboard-paste-btn` (×2 via `assertContains(count=2)` on `data-paste-target=`).
  - `test_each_field_sits_in_a_field_group`: `id_reference_number` and `id_payer_name` inputs are inside an element with class `rd-field-group` (submit handler uses `closest(".rd-field-group")`).
  - `test_no_legacy_colour_classes`: the page html contains none of `btn-outline-info`, `btn-outline-warning`, `btn-outline-primary`, `text-info`, `text-primary`, `row-cols-`.
  - `test_entry_js_uses_is_on_not_bootstrap_colours`: the page html (inline script) contains `classList.toggle("is-on"` (or `add("is-on"`) and none of `"btn-info"`, `"btn-warning"`, `"btn-outline-info"`, `"btn-outline-warning"`, `"col-sm-9"`.
  - `test_tiles_show_names`: each payment method and company name appears inside an element with class `employee-chip-label`.
- [ ] **Step 2:** `.venv/bin/python manage.py test sales.tests_employee_entry_ui` → fails (legacy classes, missing names).
- [ ] **Step 3: Markup.** Order per mockup: (1) number + payer block (in-input labels, paste buttons kept as `employee-clipboard-paste-btn`; payer prefill hint moves into the payer label line, still the same `#rd-payer-prefill-hint` element inside `#rd-payer-assist-root`); (2) payment block: head = "Where did you receive the money?" + the two toggles as small pills (`btn btn-sm rd-entry-pill`), body = `#payment-tiles` as a CSS grid (drop `row/col` wrappers), `#rd-employee-payment-hint` right under the head; (3) company block: head = "Company" + `#product-tiles-loading`, `#company-tiles` grid of horizontal tiles (icon + name); (4) product block: line tiles and package chips as two `.rd-entry-chips` rows (package chip = name + price from `data-default-price`); (5) price block: head = "Price" + `#rd-sale-preview` (`cost · profit`, hidden until computable) + notes `<details>` whose summary is a small "+ note" link; row = `#rd-duration-days-wrap` (hidden) + `#rd-sell-price-wrap` + eSIM switch + new-SIM switch (inside the existing `sales_inventory_enabled` condition); below the row the three preview notes and `#rd-days-hint`, `#rd-sim-serial-wrap`, `#rd-sim-stock-preview` (all still hidden by default); (6) dock: existing `.sticky-save` with Save (`btn-primary`) and Charge (`btn-accent`, same id/name/value/`hidden` logic).
- [ ] **Step 4: Inline script edits only where markup moved:** `setOnAccount` / `setPaidViaEmployee` toggle `is-on` instead of Bootstrap colour classes (keep `aria-pressed`, label swap, `opacity-50` → class `is-muted` on `#payment-tiles`); `syncDurationDaysVisibility` toggles `has-days` on the price row instead of rewriting `col-*` classes; `#rd-sim-stock-preview` className writes use `rd-entry-note rd-entry-note--muted|--danger|--info` instead of `text-*`; profit span uses `kpi-success` / `kpi-danger` (unchanged). No other logic edits.
- [ ] **Step 5:** `.venv/bin/python manage.py test sales core` → green (incl. `tests_charge_ui`, `EmployeeRecentSalesTests`, query budget `test_employee_entry` ≤ 8).
- [ ] **Step 6:** Commit `feat(employee): one-screen sale entry markup`.

### Task 3: Sale entry styling

**Files:**
- Modify: `static/css/employee.css`, `static/css/app.css` (delete the entry rules that move: `.sticky-save__actions`, `.employee-charge-sale-btn`, `.employee-notes-*`, `.employee-chip*`, tile resets/active frames, `#rd-pay-sub-payment-tiles` tile rules, stale `#rdCustomerPaySubModal` / `#rdEmployeeRefreshModal` rules, `.employee-packages-panel`, `.employee-package-chip`, `.rd-sim-options`, `.rd-esim-switch*`, `.rd-sale-preview`; keep `.rd-payer-*` and `.rd-recent-payer` unless restyled here)
- Test: `sales/tests_employee_entry_ui.py`

- [ ] **Step 1: Failing tests** (class `EmployeeEntryCssTests`, reading `static/css/employee.css`):
  - `test_entry_css_ellipsizes_tile_names`: the `.employee-chip-label` rule contains `text-overflow: ellipsis` and `white-space: nowrap`; `.rd-entry-tiles` children have `min-width: 0`.
  - `test_touch_targets`: `.rd-entry-tiles .employee-chip`, `.rd-entry-chips .employee-chip`, `.rd-entry-pill`, `.employee-clipboard-paste-btn` each declare `min-height: 3.45rem` (= 44 px at the 80 % root). If the one-screen check in Step 5 then fails, report the measured heights instead of shrinking targets — the controller decides.
  - `test_app_css_has_no_entry_rules`: `app.css` no longer contains `.employee-package-chip`, `#rdCustomerPaySubModal`, `.rd-esim-switch`.
- [ ] **Step 2:** Run → fail.
- [ ] **Step 3:** Style per mockup and `DESIGN.md`: blocks = `--rd-surface` card, 1px `--bs-border-color`, radius 1.2rem, padding 0.55rem, stack gap 0.45rem; in-input label = 0.72rem muted above the value inside a 3.45rem field; tiles: 1.5px border, radius 0.85rem, active = ink border + ink icon plate + 6px orange dot (`::after`), `:active` scale .95 (150 ms); chip rows: `overflow-x: auto`, hidden scrollbar, scroll-snap, active chip = `--rd-primary-bg/fg`; pills: dashed `--rd-faint` border, `is-on` = `--rd-accent` border + `--rd-accent-soft` bg + `--rd-accent-ink` text; price row = grid `1.25fr 1fr 1fr` (`has-days` adds a days column); switches = Pearl `form-switch` skin; estimate = `.tabular-nums` muted with profit coloured by `kpi-*`; dock = sticky above the tab bar, two equal buttons 3.6rem, Save ink, Charge `btn-accent`, single button full width when Charge is hidden; `#payment-tiles.is-muted` = opacity .5. Dark mode via tokens only.
- [ ] **Step 4:** `.venv/bin/python manage.py test core sales` → green.
- [ ] **Step 5: Browser check** (probe server, 375×667, both themes, Arabic): default state has `scrollHeight - innerHeight ≤ 0` with Save and the tab bar visible, `scrollWidth - innerWidth = 0`; also 430×932 and 1280×800 look right (entry centred, max-width 30rem on desktop). Record numbers in the report.
- [ ] **Step 6:** Commit `feat(employee): Pearl sale entry styling`.

### Task 4: Entry progress + valid-number tick

**Files:**
- Create: `static/js/employee-entry-progress.js` (IIFE, `htmx.onLoad` init, no globals)
- Modify: `templates/sales/employee_entry.html` (fill `{% block employee_progress %}` with `<div class="rd-entry-progress" aria-hidden="true"><i></i></div>`; step numbers `<span class="rd-entry-step" data-step="pay|co|pk|pr">`; tick `<svg class="rd-entry-valid" hidden>` inside the number field; script tag), `static/css/employee.css`
- Test: `sales/tests_employee_entry_ui.py`

**Interfaces:**
- Consumes: Task 2 classes and existing hooks (`.employee-payment-tile.is-active`, `#id_on_account`, `#id_paid_via_employee`, `.employee-company-tile.is-active`, `#id_product`, `#id_sell_price_actual`, `#id_reference_number`).

- [ ] **Step 1: Failing test** `test_progress_markup_and_script`: page has `class="rd-entry-progress"`, four `data-step=` values `pay`, `co`, `pk`, `pr`, `class="rd-entry-valid"` and `js/employee-entry-progress.js`.
- [ ] **Step 2:** Run → fail.
- [ ] **Step 3:** Implement `update()`: done steps = number valid (normalise like the submit handler: strip non-digits, `972…`→`0…`, 9 digits starting `5` → prefix `0`, then `^05[0-5]\d{7}$`), payment (active tile or on-account or to-employee), company, product (`#id_product` value), price (non-empty `#id_sell_price_actual`); bar width = done/5 (transform `scaleX`, 300 ms); step dots get `is-done`; tick `hidden` toggled. Listen to `input`/`change` on the form, `click` on the form (after the page script, via `setTimeout 0`), `htmx:afterSwap` on `#product-tiles-container`. Display only — never touches values or submit.
- [ ] **Step 4:** Tests green; commit `feat(employee): entry progress hairline`.

### Task 5: Record payment + payment edit

**Files:**
- Modify: `templates/sales/employee_record_payment.html`, `templates/sales/employee_payment_edit.html`, `templates/sales/partials/employee_payment_actions.html`, `static/js/employee-record-payment.js`, `static/css/employee.css`
- Test: `sales/tests_employee_entry_ui.py` (class `EmployeePaymentPagesTests`)

- [ ] **Step 1: Failing tests:** both pages contain every hook: `id="rd-pay-sub-page"`, `data-lookup-url=`, `id="rd-pay-sub-form"`, `id="rd-pay-sub-search"`, `id="rd-pay-sub-ac"`, `id="id_pay_sub_customer"`, `id="rd-pay-sub-balance-line"`, `id="rd-pay-sub-balance-val"`, `id="id_pay_sub_payment_method"`, `id="id_pay_sub_paid_via_employee"`, `id="rd-pay-sub-employee-toggle"`, `id="rd-pay-sub-employee-hint"`, `id="rd-pay-sub-payment-tiles"`, `data-payment-id=`, `id="rd-pay-sub-note-method"`, `id="rd-pay-sub-note-employee"`, `id="rd-pay-sub-submit"` (record page); none of `btn-outline-info`, `btn-info`, `text-info`, `text-primary`, `row-cols-`; `employee-record-payment.js` contains `"is-on"` and not `"btn-info"`.
- [ ] **Step 2:** Run → fail.
- [ ] **Step 3:** Reuse Task 2/3 building blocks: `.rd-entry-blk`, `.rd-entry-field`, `.rd-entry-tiles--pay`, `.rd-entry-pill` (`is-on`), dock with ink submit. Payment edit renders the toggle's initial state as `is-on` + `aria-pressed`. Autocomplete buttons built in JS keep `list-group-item list-group-item-action` (Bootstrap list, Pearl-skinned in CSS). "Today's payments" list → `.rd-mobile-row-card` rows.
- [ ] **Step 4:** `.venv/bin/python manage.py test sales` → green (`EmployeeRecordPaymentPageTests`, payment edit tests).
- [ ] **Step 5:** Commit `feat(employee): Pearl record/edit payment`.

### Task 6: Recent entries

**Files:**
- Modify: `templates/sales/employee_recent.html`, `templates/sales/partials/employee_recent_sale_row.html`, `templates/sales/partials/employee_recent_actions.html`, `templates/sales/partials/employee_payment_actions.html`, `templates/sales/partials/sale_esim_badge.html`, `sale_new_sim_badge.html`, `sale_loss_badge.html`, `sale_employee_payment_badge.html`, `core/tests_ui_conventions.py` (remove each migrated file from `LEGACY_BADGES`), `static/css/employee.css`

- [ ] **Step 1:** Remove the migrated files from `LEGACY_BADGES` first; `.venv/bin/python manage.py test core.tests_ui_conventions` → `test_no_bootstrap_color_badges` fails naming them.
- [ ] **Step 2:** Badges → `rd-badge rd-badge--neutral|pending|paid|cancelled` (esim / new SIM / employee payment = neutral; loss = cancelled; scope chip = neutral); Edit buttons `btn-outline-primary` → `btn-outline-secondary btn-sm`; filter form grid → `rd-filter-bar`-style stacked fields on mobile, 3 columns ≥ 768px; list rows → Pearl row (identifier `.tabular-nums`, payer, amount, status badge, actions ≥ 44px). Keep `#sale-row-<pk>`, `#payment-row-<pk>`, `.rd-charge-row`, poll attrs, delete `hx-*`, `data-rd-filter-count`, `indicator_id`.
- [ ] **Step 3:** `.venv/bin/python manage.py test core sales` → green (`EmployeeRecentSalesTests`, `EmployeeRecentChargeTests`, `test_legacy_lists_only_shrink`, budget `test_employee_recent_sales` ≤ 12; management pages using the shared badge partials still render).
- [ ] **Step 4:** Commit `feat(employee): Pearl recent entries`.

### Task 7: Check / refresh number

**Files:**
- Modify: `templates/sales/employee_refresh_number.html`, `static/css/number-check.css`, `sales/tests.py` (only the assertion `assertContains(resp, "btn-outline-primary")` → `assertContains(resp, 'id="rd-check-submit"')` plus `assertNotContains(resp, "btn-outline-primary")`), `locale/ar/LC_MESSAGES/django.po`

- [ ] **Step 1:** Change the test assertion; run `sales.tests.EmployeeRefreshPhoneTests` → the `NotContains` fails.
- [ ] **Step 2:** Buttons: refresh = `btn-primary`, check = `btn-outline-secondary`, side by side via CSS grid (no `row/col`), ≥ 44px; number field = `.rd-entry-field` with paste; result box `#rd-refresh-result` = `.rd-card`-like surface (keep `d-none`, `-title`, `-body`); hard-coded "تحديث الرقم" / "فحص الرقم" → `{% trans "Refresh number" %}` / `{% trans "Check number" %}` with those Arabic msgstrs (the test's Arabic strings must still render). Keep `#rd-refresh-page` and all its `data-*`, the CSRF input, `data-default-label`, the three script tags and the `#rd-refresh-result` before `#rd-check-result` order.
- [ ] **Step 3:** `.venv/bin/python manage.py test sales` → green except the known date flake.
- [ ] **Step 4:** Commit `feat(employee): Pearl number check`.

### Task 8: Sale edit, stock check, my balance

**Files:**
- Modify: `templates/sales/employee_sale_edit.html`, `templates/sales/employee_stock_check.html`, `templates/sales/partials/employee_stock_check_results.html`, `templates/employees/my_balance.html`, `templates/employees/partials/employee_ledger_table.html` (style only), `static/css/employee.css`
- Test: `sales/tests_employee_entry_ui.py` (class `EmployeeSecondaryPagesTests`)

- [ ] **Step 1: Failing tests:** sale edit page contains `rd-entry-blk`, each of `name="payer_name"`, `name="reference_number"`, `name="sell_price_actual"`, `name="notes"` inside a `rd-field-group`, and no `row g-3` / `col-md-6`; my balance page contains `rd-stat` (balance as the hero stat) and still `rd-employee-wallet`, "Wallet details", "Movement statement".
- [ ] **Step 2:** Run → fail.
- [ ] **Step 3:** Sale edit: Pearl blocks with `rd-field-group` + in-input labels, the info note stays `alert alert-info`, ink Save + secondary Cancel in the dock. Stock check: search field = `.rd-entry-field`, result cards Pearl (already `rd-mobile-row-card`). My balance: balance as `.rd-stat--hero`, salary as a normal `.rd-stat`, headings via `rd-heading-*`; ledger rows keep their markup hooks.
- [ ] **Step 4:** `.venv/bin/python manage.py test sales employees core` → green (`EmployeeStockCheckTests`, `EmployeeMyBalanceTests`).
- [ ] **Step 5:** Commit `feat(employee): Pearl sale edit, stock check, my balance`.

### Task 9: Phase verification

- [ ] **Step 1:** Full suite green (date flake excepted on 2026-10-15 only).
- [ ] **Step 2:** Probe server, every Phase 1 page at 375×667 / 430×932 / 1280×800, light + dark, Arabic (+ English spot-check of entry): screenshots; `scrollWidth - innerWidth = 0` everywhere; entry default state fits one screen at 375×667 (record `scrollHeight` vs baseline); Review Focus 2 and 4 checked by hand (select a company → products swap → progress resets; on-account and to-employee pills in dark).
- [ ] **Step 3:** Fill "After" below; commit `docs: Pearl phase 1 after-numbers`.

## Baseline / After (filled by Task 0 / Task 9)

| Page | Measure | Before | After |
|---|---|---|---|
| Employee entry 375×667 | `scrollHeight` / visible height | | |
| Employee entry 375×667 | Save visible without scroll | | |
