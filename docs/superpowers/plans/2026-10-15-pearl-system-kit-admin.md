# Pearl System — Page Kit + Admin Batch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One shared page kit (header, widths, form fields, file picker, filter card, sortable headers, row actions, empty rows) and the 13 admin pages rebuilt on it, so every admin screen looks and behaves the same.

**Architecture:** A new template-tag library `core/templatetags/kit.py` (`{% load kit %}`) plus partials in `templates/partials/kit/`, with CSS in `static/css/design-system.css` (`/* Page kit */` section). Pages keep their views, URLs, context and displayed data; only markup changes, plus the small UX fixes listed per task. Convention tests get shrink-only `LEGACY_*` sets so un-migrated pages (companies, sales, reports…) keep passing and later batches shrink them.

**Tech Stack:** Django 4.2 templates + custom tags, Bootstrap 5.3.3, htmx 1.9.12 (hx-boost), Pearl tokens (`--rd-*`).

**Spec:** Owner direction (2026-10-15): "بدي كل النظام … شيء شامل" — pages are disorganised and impractical; start with admin; continuous review; small UX changes allowed, nothing displayed removed. Evidence: `.superpowers/admin-inventory.md` (code survey), visual audit summary in "Problems per page" below. Rules: `.cursor/rules/pearl-ui.mdc`, `.cursor/rules/ui-navigation-and-toasts.mdc`, `DESIGN.md`.

## Global Constraints

- Never touch `db.sqlite3`. Browser checks use the probe server (`PYTHONPATH=.superpowers DJANGO_SETTINGS_MODULE=probe_settings .venv/bin/python manage.py runserver 127.0.0.1:8765`, users `admin` / `ياسمين`, password `pearl-probe-1`).
- Never remove displayed information, links or actions. Moving, restyling or re-grouping is fine.
- No view/URL/business-logic changes beyond those named in a task. Context variable names stay.
- Colours only from `--rd-*` tokens; new token ⇒ both theme blocks + a `DESIGN.md` row. No raw hex outside token blocks.
- Primary action = ink `btn-primary`; orange = accent only; errors = `--rd-danger`; status = `rd-badge rd-badge--*`.
- Every user-visible string via `{% trans %}` / `gettext`, with Arabic in `locale/ar/LC_MESSAGES/django.po`, then `.venv/bin/python manage.py compilemessages -l ar`.
- RTL first: logical properties only (`margin-inline-start`, `inset-inline-end`…), no `left`/`right`.
- Touch targets ≥ 44px (3.45rem at the 80% root) below 992px; text ≥ 4.5:1; visible `:focus-visible`; motion 150–450 ms transform/opacity only.
- No readable text below 0.8rem (≈10.2px) in kit components.
- Messages via Django `messages`; form-wide errors via `partials/form_errors_toast.html`; no `alert-success/danger` boxes.
- Full suite: `.venv/bin/python manage.py test` (known date flake: `sales.tests_number_check.HappyPathTests` 2 failures on 2026-10-15 only). JS: `for f in tests/js/*.test.js; do node $f; done`.
- Out of scope: `phone_refresh/templates/phone_refresh/public_refresh.html`.

## Review Focus

- Bound forms with errors: the kit field must keep the user's typed value, show the field error under the field (`--rd-danger`) and mark the control `is-invalid` + `aria-invalid="true"`.
- `ClearableFileInput` with an existing file: the kit picker must still post `<name>-clear` and show the current file name, otherwise avatars can't be removed.
- Date inputs with an initial `date` value must render `value="YYYY-MM-DD"` (browsers drop any other format, silently blanking the field on edit pages).
- Filter card on a page that carries non-filter params (`tab`, `sort`, `order`, `page`, `partial`) must count only the form's own fields as active (today employee detail shows "4" with no filters).
- Multi-line help text (Django's password rules `<ul>`) must render as a compact list, not push the layout with a 20px gap.

---

## File Structure

| File | Responsibility |
|---|---|
| `core/templatetags/kit.py` (create) | `rd_field`, `rd_fields`, `rd_page_header` (block tag), `rd_filter_card`, `rd_sort_link`, `rd_widget_attrs` helper |
| `templates/partials/kit/field.html` (create) | One field: label, control, help, errors |
| `templates/partials/kit/file_field.html` (create) | Arabic file picker built from the bound field (wraps `rd-file-pick`) |
| `templates/partials/kit/fields.html` (create) | Hidden fields + non-field toast + grid of `field.html` |
| `templates/partials/kit/page_header.html` (create) | Header markup used by the block tag |
| `templates/partials/kit/filter_card.html` (create) | Collapsible filter card with fields + Apply/Reset |
| `templates/partials/kit/row_actions.html` (create) | Edit link + delete POST (icon on desktop, text in mobile cards) |
| `templates/partials/kit/empty_row.html` (create) | `<tr><td colspan>` around `partials/empty_state.html` |
| `static/css/design-system.css` (modify) | `/* Page kit */` section: `.rd-page*`, `.rd-page-head*`, `.rd-form-grid`, `.rd-field*`, `.errorlist`, `.form-text`, `.rd-help`, filter grid, `.rd-icon-btn` size |
| `templates/partials/pagination.html` (modify) | per-page links reset the given `page_key` (fixes `payments_page`) |
| `core/tests_kit.py` (create) | Tag/partial tests |
| `core/tests_ui_conventions.py` (modify) | New shrink-only sets + rules |
| `DESIGN.md` (modify) | "Page kit" section: when to use each piece |
| admin templates (modify) | Tasks 5–8 |

---

### Task 1: Form field kit (`rd_field`, `rd_fields`, file picker, field CSS)

**Files:**
- Create: `core/templatetags/kit.py`, `templates/partials/kit/field.html`, `templates/partials/kit/fields.html`, `templates/partials/kit/file_field.html`, `core/tests_kit.py`
- Modify: `static/css/design-system.css` (new `/* Page kit */` section at the end, before any `@media print`), `DESIGN.md`, `locale/ar/LC_MESSAGES/django.po`

**Interfaces:**
- Produces:
  - `{% rd_field field size="" full=False label=None help=None %}` — inclusion tag → `partials/kit/field.html`.
  - `{% rd_fields form size="" full="" exclude="" %}` — inclusion tag → `partials/kit/fields.html`; `full` is a space-separated list of field names that span both columns, `exclude` a space-separated list of names to skip (rendered elsewhere with `rd_field`); renders `form.hidden_fields`, the non-field toast, then `<div class="rd-form-grid">` of `rd_field`.
  - `rd_widget_attrs(bound_field, size: str) -> dict` (module-level helper in `kit.py`): classes by widget type — `Select`/`SelectMultiple` → `form-select` (+`form-select-sm`), `CheckboxInput` → `form-check-input` (+ keeps `role=switch` if present), file inputs → `visually-hidden`, everything else → `form-control` (+`form-control-sm`); merges with existing `class` without duplicates; adds `is-invalid` + `aria-invalid="true"` when the field has errors; `aria-describedby` = help id and/or error id (`<auto_id>-help`, `<auto_id>-error`).
  - Field wrapper classes: `.rd-field` (+ `.rd-field--check` for checkboxes, `.rd-field--full` for full width / `Textarea` / file / any field whose help contains `<ul`).
  - Label: `<label class="form-label" for=…>{{ field.label }}</label>` — no colon; required fields get `<span class="rd-field__req" aria-hidden="true">*</span>`.
  - Help: `<div class="form-text rd-help" id="…-help">{{ help|safe }}</div>` (help defaults to `field.help_text`).
  - Errors: `<div class="rd-field__error" id="…-error">` one line per error.
  - `DateInput`: render with `format="%Y-%m-%d"` (set `widget.format` on a copy, never mutate the form class's widget).
  - File picker (`file_field.html`): `<label class="rd-file-pick" data-rd-file-pick>` containing the real input (from `field.as_widget(attrs=…)`, keeps `name`/`accept`), an icon, `<span data-rd-file-name>` with `{% trans "Choose a file or drop it here" %}`, and when the bound field has an initial file: current file name + a `form-check` checkbox named `<field.html_name>-clear` labelled `{% trans "Remove current file" %}` (only if the widget is `ClearableFileInput` and the field is not required).

- [ ] **Step 1: Write failing tests in `core/tests_kit.py`** (render via `Template("{% load kit %}…").render(Context({...}))` with small throw-away `forms.Form` classes defined in the test module):

```python
class FieldTests(SimpleTestCase):
    def test_text_field_gets_form_control_label_without_colon(self):
        html = render_field(F()["name"])
        self.assertIn('class="form-label"', html)
        self.assertNotIn(":</label>", html)
        self.assertRegex(html, r'<input[^>]*class="[^"]*\bform-control\b')
    def test_size_sm_adds_small_classes(self):  # form-control-sm / form-select-sm
    def test_select_gets_form_select_not_form_control(self):
    def test_existing_widget_class_is_kept_once(self):  # widget attrs class="form-control x" → "form-control x" exactly once each
    def test_checkbox_renders_form_check(self):  # .rd-field--check, form-check-input, form-check-label
    def test_bound_error_marks_invalid_and_keeps_value(self):
        f = F(data={"name": "abc", "amount": "x"}); f.is_valid()
        html = render_field(f["amount"])
        self.assertIn("is-invalid", html); self.assertIn('aria-invalid="true"', html)
        self.assertIn('value="x"', html); self.assertIn("rd-field__error", html)
    def test_help_text_linked_by_aria_describedby(self):
    def test_password_help_list_makes_field_full_width(self):  # help_text "<ul><li>a</li></ul>" → rd-field--full
    def test_date_initial_renders_iso_value(self):  # initial=date(2026, 9, 3) → value="2026-09-03"
    def test_textarea_is_full_width(self):
    def test_required_marker(self):
class FileFieldTests(SimpleTestCase):
    def test_file_input_wrapped_in_arabic_picker(self):  # data-rd-file-pick, data-rd-file-name, visually-hidden input, no "Currently"
    def test_clearable_with_initial_shows_name_and_clear_checkbox(self):  # name="<html_name>-clear"
    def test_required_file_has_no_clear_checkbox(self):
class FieldsTests(SimpleTestCase):
    def test_hidden_fields_rendered_and_grid_wraps_visible(self):  # rd-form-grid; hidden input present outside grid
    def test_non_field_errors_go_to_toast(self):  # data-rd-flash (form_errors_toast.html) contains the error text
    def test_full_names_span(self):  # full="notes" → notes wrapper has rd-field--full
    def test_exclude_skips_fields(self):  # exclude="is_active" → no is_active input
```

- [ ] **Step 2: Run** `.venv/bin/python manage.py test core.tests_kit -v 2` — Expected: FAIL (`'kit' is not a registered tag library`).
- [ ] **Step 3: Implement** `kit.py` helpers/tags and the three partials as specified in Interfaces.
- [ ] **Step 4: CSS** in `design-system.css` `/* Page kit */`:
  - `.rd-form-grid`: `display:grid; gap: 1.1rem 1.25rem;` one column; `@media (min-width: 768px)` two columns; `.rd-field--full { grid-column: 1 / -1; }`.
  - `.rd-field .form-label` 0.88rem, `font-weight: 600`, `color: var(--rd-ink)`, margin-bottom 0.35rem; `.rd-field__req` `color: var(--rd-danger)`, `margin-inline-start: 0.2rem`.
  - `.rd-field__error, .errorlist` (global, so un-migrated forms also benefit): `list-style:none; padding:0; margin:0.35rem 0 0; color: var(--rd-danger); font-size: 0.82rem;`.
  - `.form-text, .rd-help` 0.82rem, `color: var(--rd-muted)` (check the muted token name in the light block), `.rd-help ul { margin: 0.3rem 0 0; padding-inline-start: 1.1rem; } .rd-help li + li { margin-top: 0.1rem; }`.
  - `textarea.form-control { resize: vertical; }`.
  - `.rd-field--check` aligns the checkbox and its label on one line with `gap: 0.5rem`, min-height 2.6rem (44px on touch via `@media (max-width: 991.98px) { min-height: 3.45rem }`).
- [ ] **Step 5: i18n** — add Arabic for "Choose a file or drop it here" → "اختر ملفاً أو اسحبه هنا", "Remove current file" → "إزالة الملف الحالي", "Current file" → "الملف الحالي"; `compilemessages -l ar`.
- [ ] **Step 6: DESIGN.md** — new section "Page kit" with a "Forms" subsection: use `{% rd_fields form %}` / `{% rd_field %}`; never `label_tag`; file inputs always through the kit.
- [ ] **Step 7: Run** `.venv/bin/python manage.py test core` — Expected: PASS.
- [ ] **Step 8: Commit** `feat(kit): form fields, Arabic file picker, error and help styling`.

### Task 2: Page header + page widths

**Files:**
- Modify: `core/templatetags/kit.py`, `static/css/design-system.css`, `DESIGN.md`, `core/tests_kit.py`, `.po`
- Create: `templates/partials/kit/page_header.html`

**Interfaces:**
- Produces:
  - Block tag `{% rd_page_header title subtitle="" back_url="" back_label="" meta="" %}…actions HTML…{% end_rd_page_header %}` (all args template expressions; the body is the actions slot and may be empty). Renders:
    ```html
    <header class="rd-page-head">
      <a class="rd-page-head__back" href="{back_url}">{arrow svg}{back_label or "Back"}</a>   <!-- only if back_url -->
      <div class="rd-page-head__row">
        <div class="rd-page-head__titles"><h1 class="rd-heading-xl">{title}</h1><p class="rd-page-head__sub">{subtitle}</p></div>
        <div class="rd-page-head__aside"><span class="rd-page-head__meta tabular-nums">{meta}</span>{actions}</div>
      </div>
    </header>
    ```
    (`p`, `meta`, `aside` omitted when empty; back arrow points to the inline-start side in RTL via `transform: scaleX(-1)` under `[dir="rtl"]`.)
  - Width wrappers: `.rd-page` (full content width, the default for lists, reports, detail pages), `.rd-page--form` (`max-width: 56rem; margin-inline: auto`), `.rd-page--settings` (`max-width: 76rem; margin-inline: auto`). `.rd-admin-page` and `.rd-admin-page--wide` stay in `app.css` (other areas still use them) — admin pages stop using them in Tasks 5–8.
- CSS: `.rd-page-head` margin-bottom 1.25rem; `__back` is an inline flex link, `color: var(--rd-muted)`, 0.88rem, min-height 2.2rem (3.45rem below 992px), hover ink, `:focus-visible` ring; `__row` flex, wrap, `justify-content: space-between`, `align-items: flex-end`, gap 0.75rem 1.5rem; `__sub` 0.92rem muted, margin 0.25rem 0 0; `__aside` flex wrap gap 0.5rem, align center; `__meta` 0.88rem muted. Below 576px the aside takes the full row and its buttons stretch (`flex: 1 1 auto`).

- [ ] **Step 1: Failing tests** in `core/tests_kit.py`:
```python
class PageHeaderTests(SimpleTestCase):
    def test_title_only(self):  # one h1.rd-heading-xl inside header.rd-page-head; no rd-page-head__back, no __sub
    def test_back_link_subtitle_meta_and_actions(self):  # href, label, sub text, meta text, body html present inside __aside
    def test_title_is_escaped(self):  # title "<b>x</b>" → "&lt;b&gt;"
```
- [ ] **Step 2: Run** `.venv/bin/python manage.py test core.tests_kit` — Expected: FAIL.
- [ ] **Step 3: Implement** the block tag (a `template.Node` that resolves args, renders its nodelist as `actions`, then renders `partials/kit/page_header.html`), the partial and CSS. "Back" → "رجوع" in `.po`.
- [ ] **Step 4: DESIGN.md** "Page kit → Header and width": every management page starts with `{% rd_page_header %}` inside one `.rd-page` wrapper; forms use `--form`, settings/branding use `--settings`.
- [ ] **Step 5: Run** `.venv/bin/python manage.py test core` — PASS. **Commit** `feat(kit): page header block and page widths`.

### Task 3: Filter card + sortable headers + row actions + empty rows + pagination fix

**Files:**
- Modify: `core/templatetags/kit.py`, `templates/partials/pagination.html`, `static/css/design-system.css`, `static/css/app.css` (`.rd-icon-btn` size), `core/tests_kit.py`, `DESIGN.md`, `.po`
- Create: `templates/partials/kit/filter_card.html`, `templates/partials/kit/row_actions.html`, `templates/partials/kit/empty_row.html`

**Interfaces:**
- Consumes: `rd_field` (Task 1), `partials/filter_card_open.html` / `filter_card_close.html` (existing, args `filter_title`, `active_count`, `indicator_id`, `card_open`).
- Produces:
  - `{% rd_filter_card form action="" reset_url="" hx_target="" indicator_id="" keep="" title="" open=None %}` (takes context) → `partials/kit/filter_card.html`. `keep` = space-separated GET keys re-posted as hidden inputs from `request.GET` (e.g. `"sort order tab"`). Active count = number of the **form's own field names** with a non-empty value in `request.GET`. Form markup: `<form method="get" class="rd-filter-grid" …>` with hx attributes only when `hx_target` given (`hx-get=action`, `hx-target`, `hx-push-url="true"`, `hx-indicator`); fields via `rd_field … size="sm"`; a search-like first field (`name` in `q`, `search`) gets `rd-field--wide`; buttons in `.rd-filter-grid__actions`: Apply `btn btn-primary btn-sm` + Reset `btn btn-outline-secondary btn-sm` link (`reset_url`). Non-field errors render inline under the grid as `.rd-field__error` (fixes silently dropped date-range errors).
  - CSS `.rd-filter-grid { display:grid; gap: 0.85rem 1rem; grid-template-columns: repeat(auto-fill, minmax(min(100%, 11.5rem), 1fr)); align-items: end; }` `.rd-field--wide { grid-column: span 2; }` (span 1 below 576px) `.rd-filter-grid__actions { display:flex; gap:.5rem; justify-content:flex-end; grid-column: -2 / -1 }` — actions always sit on the last column, aligned with the fields' bottom edge.
  - `{% rd_sort_link key label sort order page_key="page" sort_param="sort" order_param="order" %}` (takes context) → returns the `<a class="rd-datagrid-sort[ is-active is-asc|is-desc]" href="?…">label</a>` with `querystring` semantics: clicking the active asc column → desc, anything else → asc; resets `page_key` to 1. Must produce byte-identical hrefs to the existing hand-written headers in `templates/accounts/partials/user_list_results.html` (test against one).
  - `partials/kit/row_actions.html` args `edit_url`, `delete_url`, `confirm` (text, escaped into `hx-confirm` with `force_escape`), `next` (optional hidden input), `hx_target` + `hx_swap` (optional; when given the delete form uses `hx-post`/`hx-target`/`hx-swap` instead of a boosted POST), `variant` (`"icon"` default | `"text"` for mobile cards), `edit_label`/`delete_label` (default "Edit"/"Delete"). Icon variant: `.rd-row-actions` with `a.rd-icon-btn` (pencil svg, `aria-label` + `title`) and `form` + `button.rd-icon-btn.rd-icon-btn--danger` (trash svg). Edit link keeps `hx-boost="false" data-rd-page` (as today). Either action omitted when its URL is empty.
  - `.rd-icon-btn` → 2.4rem square (≥ 3.45rem below 992px).
  - `partials/kit/empty_row.html` args `colspan`, `title`, `text`, `icon` → `<tr class="rd-empty-row"><td colspan>{% include "partials/empty_state.html" %}</td></tr>`.
  - `pagination.html`: per-page links reset `page_key` (whatever it is) instead of hard-coding sibling keys; keep the existing `pagination_reset_sibling_pages` behaviour for keys it already handles.

- [ ] **Step 1: Failing tests** (`core/tests_kit.py`, using `RequestFactory` for context):
```python
class FilterCardTests(SimpleTestCase):
    def test_counts_only_form_fields(self):  # GET ?tab=x&sort=a&order=asc&page=2&q=ab → badge count 1
    def test_keep_reposts_hidden_inputs(self):  # keep="sort order" → hidden inputs with GET values
    def test_htmx_attrs_only_with_target(self):
    def test_non_field_error_visible(self):
    def test_fields_small_and_without_colons(self):
class SortLinkTests(SimpleTestCase):
    def test_inactive_column_sorts_asc_and_resets_page(self):
    def test_active_asc_toggles_to_desc(self):  # class has is-active is-asc
class RowActionsTests(SimpleTestCase):
    def test_icon_variant_has_labels_and_escaped_confirm(self):  # aria-label, title, hx-confirm escaped quotes
    def test_omits_delete_without_url(self):
class PaginationTests(TestCase):
    def test_per_page_links_reset_custom_page_key(self):  # page_key="payments_page" → per_page link contains payments_page=1
```
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4:** DESIGN.md "Page kit → Filters, tables, row actions". **Step 5:** `.venv/bin/python manage.py test core` PASS. **Commit** `feat(kit): filter card, sort links, row actions, empty rows; pager resets any page key`.

### Task 4: Convention guards (shrink-only sets)

**Files:** Modify `core/tests_ui_conventions.py`.

**Interfaces:** Produces four new shrink-only sets, generated from the current tree (list every offending template under `templates/` except the out-of-scope file), each with a test that fails when a file **not** in the set offends:
- `LEGACY_PAGE_HEAD` — management/employee page templates (those that `{% extends` a shell and define `{% block content %}`) whose source lacks `rd_page_header`.
- `LEGACY_APP_CARD` — templates containing `app-card` or `class="card` / `class="card ` (Bootstrap card).
- `LEGACY_LABEL_TAG` — templates containing `.label_tag`.
- `LEGACY_INLINE_WIDTH` — templates with `style="` containing `max-width`.
Plus a test per set that every listed file still exists **and still offends** (so migrated files must be removed — same pattern as existing `LEGACY_*` tests; copy it).

- [ ] **Step 1:** Write the tests + sets (compute the lists with a quick `rg` and paste them sorted). **Step 2:** Run `.venv/bin/python manage.py test core.tests_ui_conventions` — PASS. **Step 3: Commit** `test(kit): shrink-only guards for headers, legacy cards, label_tag, inline widths`.

---

### Task 5: Users, user form, account settings

**Files:** `templates/accounts/user_list.html`, `templates/accounts/partials/user_list_results.html`, `templates/accounts/user_form.html`, `templates/accounts/account_settings.html`, `accounts/forms.py` (field order only), `core/tests_ui_conventions.py` (remove migrated files from sets), `accounts/tests*.py` (adjust selectors only if they assert old markup).

**Interfaces:** Consumes Tasks 1–3.

Changes:
- **user_list**: `.rd-page` (full width — owner wants wide screens used) + `rd_page_header` (title "Users", action "Create user"); toolbar search unchanged but add the clear "×" link like employees; sortable headers via `rd_sort_link`; row actions via `row_actions.html` (icon desktop, text mobile); empty desktop/mobile via `empty_row.html` / `empty_state.html`.
- **user_form** (create + edit): `.rd-page--form`; header with `back_url` = user list, `back_label` "Users", subtitle = `@username` on edit; the role badge moves into the header actions slot as `rd-badge rd-badge--neutral`; identity block stays at the top of the card. Card = `.rd-card > .rd-card-body`; fields via `{% rd_fields form full="avatar" %}` inside `form[novalidate]`. **Field order** (set `field_order` on both forms in `accounts/forms.py`, no other form change): identity (name fields, username), role, password fields, avatar, then the two lock checkboxes together at the end under a small `h2.rd-heading-lg` "Access" (`rd_fields … exclude="is_active is_active_profile"`, then the two checkboxes with `rd_field`; use the forms' real field names). Footer `.rd-form-actions` (new CSS in kit: flex, gap .5rem, `justify-content:flex-start`, top border, padding-top 1rem): Save `btn-primary` + Cancel `btn-outline-secondary`. Danger zone → `.rd-card.rd-set-section--danger` look (reuse `rd-set-section`/`__head`/`rd-set-icon--danger`), keep the blocked-reason `alert-warning`.
- **account_settings**: `.rd-page--form`; `rd_page_header` (title "Account settings"); keep both forms and the hidden `form=` discriminator; fields via `rd_fields` (avatar through the kit picker; the hard-coded "PNG or JPG" hint passed as `help` to `rd_field`). Second card heading stays. The double-h1 in the employee shell (`employee_header` block) must end with exactly one `h1` on the page in both shells (check the rendered HTML in both).
- Remove the migrated files from every `LEGACY_*` set they're in.

- [ ] **Step 1:** Add tests in `accounts/tests.py` (or the existing accounts test module): `test_user_form_uses_kit_and_arabic_picker` (GET edit page → contains `rd-page-head`, `data-rd-file-pick`, no `Currently:`), `test_account_settings_single_h1` (both shells: `html.count("<h1") == 1`), `test_user_form_field_order` (index of `username` < `role` < `password`-ish < lock checkboxes). Run — FAIL.
- [ ] **Step 2:** Implement the template changes. **Step 3:** `.venv/bin/python manage.py test accounts core` — PASS. **Step 4: Commit** `feat(admin): users and account settings on the page kit`.

### Task 6: Employees list, detail, form

**Files:** `templates/employees/employee_list.html`, `employee_detail.html`, its 4 partials, `employee_form.html`, `employees/views.py` (only the filter `active_count` context values, which become unused — remove them only if nothing else reads them), `core/tests_ui_conventions.py`, employee tests.

Changes:
- **employee_list**: `.rd-page` + header (action "Add employee"); stat strip stays; toolbar: search left as is; the salary-accrual form gets a visible label, `hx-confirm` ("Run salary accrual for the selected month?" / "تشغيل استحقاق الرواتب للشهر المحدد؟") and `btn btn-outline-secondary btn-sm`; `.rd-person__stat` labels ≥ 0.8rem.
- **employee_detail**: `.rd-page` + header (title name, subtitle `@username`, back to employees, action Edit `btn-outline-secondary btn-sm`). Summary row becomes an `rd-stat-strip` with balance (`kpi-expense` / `kpi-success`, not `text-success`) and monthly salary — same values. Tabs unchanged (`rd-section-tabs`). Both filter cards → `{% rd_filter_card payments_filter_form … keep="tab payments_sort payments_order partial" %}` / ledger equivalent (use the real context names from the view). Adjustment card: `rd_fields` with help text shown, Save `btn-primary btn-sm` (not `w-100` on desktop). Ledger delete → `row_actions.html` with `delete_url` only (`hx_target="body" hx_swap="outerHTML"` to keep today's behaviour). Sort headers via `rd_sort_link` with the tab-specific params. Empty rows via `empty_row.html`. Pagination with `page_key` (now fixed for `payments_page`).
- **employee_form**: `.rd-page--form`, header with back to list (or detail on edit), `rd_fields`, `.rd-form-actions` Save + Cancel. Select empty label `---------` → add `empty_label` with `gettext_lazy("Choose…")` in `EmployeeProfileForm` (the only form change).

- [ ] **Step 1:** Tests: `test_detail_filter_badge_counts_only_filters` (GET `?tab=ledger` → no active badge number), `test_detail_date_range_error_visible` (from > to → error text in response), `test_accrual_has_confirm`, `test_employee_form_on_kit`. Run — FAIL.
- [ ] **Step 2:** Implement. **Step 3:** `.venv/bin/python manage.py test employees core` — PASS. **Commit** `feat(admin): employees on the page kit; accrual confirm; filter errors shown`.

### Task 7: System settings, site branding, search

**Files:** `templates/core/system_settings.html`, `_system_settings_general.html`, `_system_settings_database.html`, `site_branding.html`, `_branding_drop.html`, `search.html`, `core/views.py` (restore: stop the duplicate toast loop at the lines that push each field error as `messages.error`, since errors now show inline), `static/css/app.css` (savebar), `core/tests_ui_conventions.py` (remove `core/search.html` from `LEGACY_BADGES` and `LEGACY_TABLE_LIGHT`), core tests.

Changes:
- **system_settings**: `.rd-page--settings` + header; tabs → `ul.nav.nav-tabs.rd-section-tabs` (same `?tab=` links, `aria-current`). General tab sections unchanged in content. Restore form: file picker through `{% rd_field restore_form.file %}` (keep `data-rd-confirm-gate` working: the gate reads `input[type=file]` inside the form — still true).
- **Savebar always visible** (settings + branding): `.rd-savebar` is shown by default in a calm state ("No unsaved changes" / "لا توجد تغييرات غير محفوظة", Save disabled); when dirty it switches to the current highlighted state and enables Save. Update `admin-ui.js` `initDirtyForm` accordingly and its JS test in `tests/js/` if one covers it.
- **site_branding**: `.rd-page--settings` + header; keep layout and preview; hand-written text inputs keep their `data-rd-bind` attributes but take class/size from the kit (`rd_field` cannot add `data-*`; keep the hand-written inputs, only align label markup: `label.form-label` without colon, help via `.form-text`). "Remove current image" checkbox: ≥ 0.85rem label, normal ink colour.
- **search**: `.rd-page` + header (title "Search", subtitle = the result count line); search form → one row: input (`flex:1`) + button **beside** it (`.rd-search-row`, flex, gap .5rem; stacks below 576px); the three result tables → `rd-table-modern` in `.rd-data-shell` with a mobile card stack (`rd-table-dual` pattern from users); count chips → `rd-badge rd-badge--neutral`; status → `{% include "partials/sale_status_badge.html" %}`; balances `kpi-expense`/`kpi-success`; refs monospace in ink (no magenta: find the class and drop the colour); "View all matches" link `?q={{ q|urlencode }}`; no-results → `partials/empty_state.html`; define nothing called `rd-heading-md` — use `rd-heading-lg`.
- Database restore duplicate errors: errors appear once (inline).

- [ ] **Step 1:** Tests: `test_search_uses_modern_tables_and_rd_badges` (no `table-light`, no `badge bg-`), `test_search_view_all_link_urlencodes_q` (`q="a b&c"`), `test_system_settings_tabs_are_section_tabs`, `test_restore_errors_not_duplicated_as_toasts`, JS test for the always-visible savebar (calm state → Save disabled; after input → enabled). Run — FAIL.
- [ ] **Step 2:** Implement. **Step 3:** `.venv/bin/python manage.py test core` + JS tests — PASS. **Commit** `feat(admin): settings, branding and search on the page kit; visible savebar`.

### Task 8: Expenses list, form, report + audit log

**Files:** `templates/expenses/expense_list.html`, `partials/expense_list_results.html`, `expense_form.html`, `expense_report.html`, `expenses/views.py` (move the inline report form class to `expenses/forms.py` as `ExpenseReportFilterForm`, same fields), `templates/audit/audit_log_list.html`, `audit/forms.py` (create `AuditLogFilterForm` with the same GET names/choices the template hand-writes today: `q`, actor, model, action, `date_from`, `date_to` — read the view for exact names), `audit/views.py` (build the form from `request.GET` for rendering only; keep the existing parsing/filtering untouched), `static/css/app.css` (`.rd-audit*` sizes, diff table), tests.

Changes:
- **expense_list**: `.rd-page` + header (subtitle kept, action "New expense"); `rd_filter_card` (fixes colon labels and button sizes); sort via `rd_sort_link`; row actions icon variant; category stays `rd-badge--neutral`; amount stays `kpi-expense`.
- **expense_form**: `.rd-page--form`, header (back to expenses; title unchanged), `rd_fields full="notes"`, `.rd-form-actions`. No new actions (no delete on the edit page), no currency suffix.
- **expense_report**: `.rd-page` + header with the date filter in the actions slot (`rd-report-filter` kept, labels above inputs on phone, inputs ≥ 9rem so dates aren't clipped); category breakdown unchanged (inline width style stays — data-driven); table gets the mobile card stack; row actions → icon variant (keeps both edit and delete — nothing removed); empty → `empty_row.html`.
- **audit_log_list**: `.rd-page` + header with `meta` = "N events"; filter → `rd_filter_card AuditLogFilterForm`; timeline text ≥ 0.82rem, refs/timestamps `color: var(--rd-muted)` with ≥ 4.5:1; day dividers 0.85rem semibold; diff table gets `rd-table-modern table-sm`; action chips: create = `rd-badge--paid`, update = `rd-badge--pending`, delete = `rd-badge--cancelled`, other = `--neutral` (map in the template by action code, check the view/model for the codes).
- Remove migrated files from every `LEGACY_*` set.

- [ ] **Step 1:** Tests: `test_expense_report_form_lives_in_forms` (import `ExpenseReportFilterForm`), `test_expense_filter_labels_without_colon`, `test_audit_filter_form_keeps_get_names` (rendered input names equal the old ones), `test_audit_action_badges`. Run — FAIL.
- [ ] **Step 2:** Implement. **Step 3:** `.venv/bin/python manage.py test expenses audit core` — PASS. **Commit** `feat(admin): expenses and audit log on the page kit`.

### Task 9: Verification + final review

- [ ] **Step 1:** Full suite `.venv/bin/python manage.py test` and JS tests — PASS (only the known date flake allowed).
- [ ] **Step 2:** Restart the probe server; browser pass over all 13 admin pages at 1680×950 and 375×812, light and dark (set `data-bs-theme` on `<html>` via JS, don't persist): `scrollWidth - innerWidth == 0`, one `h1`, no `Choose File` text, no label ending with `:`, header present, actions visible. Fix anything found (with a test when it's logic).
- [ ] **Step 3:** Whole-branch review by a fresh reviewer (claude-fable-5-1-thinking-high); fix findings.
- [ ] **Step 4:** Commit; screenshots for the owner (Arabic summary). Next plan: companies (Layan/Sky reconcile first), then sales/customers, reports, phone_refresh + sms_gateway, employee pages + recent-entries pager.

---

## Known limitation (tell the owner)

Native `<input type="date">` / `type="month"` show their text in the **browser's** language and order (e.g. `mm/dd/yyyy` on an English Chrome). The page cannot change that; an Arabic Chrome shows Arabic. A custom date picker is a bigger change — ask before doing it.
