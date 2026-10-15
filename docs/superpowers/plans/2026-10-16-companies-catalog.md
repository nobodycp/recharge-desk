# Companies Batch 2 — Payment Methods + Product Forms + Product List

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put payment methods and the product catalog (list + line/variant forms) on the page kit, matching the company list/form patterns already on main.

**Architecture:** Templates and small form/view glue only. Keep URLs, GET/POST names, query annotations, and displayed fields. Delete UI follows company_list: hide Delete when not deletable; explain on the edit page. Product list keeps its company/line `<details>` structure and the pinned `rd-product-line__details` / `open` class.

**Tech Stack:** Django 4.2, `{% load kit %}`, Pearl tokens, htmx hx-boost.

**Spec:** Owner direction (continuous Pearl migration); design approved 2026-10-16 in chat (payment methods → line/variant forms → product list; company_detail deferred). Inventory: `.superpowers/companies-inventory.md`. Reference: `templates/companies/company_form.html`, `company_list.html`, `templates/accounts/user_list.html`.

## Global Constraints

- Never touch `db.sqlite3`. Probe: `PYTHONPATH=.superpowers DJANGO_SETTINGS_MODULE=probe_settings .venv/bin/python manage.py runserver 127.0.0.1:8765` (admin / ياسمين, `pearl-probe-1`).
- Never remove displayed information, links or actions (except hide Delete when not deletable — already the owner rule for companies).
- Keep URL names, form field names, filter GET names (`q`, `company`, `show_archived`), `expand_packages` behaviour, `class="rd-product-line__details"` and `open` when searching.
- Colours only from `--rd-*`; no `badge text-bg-*` / `bg-*`; no `btn-outline-warning` for Archive (use `btn-outline-secondary` or `btn-outline-primary` — never orange for warnings).
- Strings via `{% trans %}` / gettext + Arabic in `locale/ar/LC_MESSAGES/django.po` + `compilemessages -l ar`.
- Shrink-only `LEGACY_*` in `core/tests_ui_conventions.py`; never add.
- Full suite before each commit's final verify: `.venv/bin/python manage.py test`.

## Review Focus

- `ClearableFileInput` on payment method / product line icons: kit file picker still posts `<name>-clear` and shows the current file.
- Product list query count: `test_product_list_does_not_run_per_row_exists_query` must stay green (annotations already exist — do not reintroduce per-row `is_deletable` queries).
- Payment method delete with references: ProtectedError path still flashes the existing English msgid; list/edit hide Delete when referenced so the common path never hits it.
- Archive/Restore still work from the list and from the variant edit page after the button class change.

---

## File Structure

| File | Responsibility |
|---|---|
| `templates/sales/payment_method_list.html`, `payment_method_form.html` | Kit list + form |
| `sales/models.py`, `sales/views/payment_methods.py` | `is_deletable` (+ annotate on list) |
| `templates/companies/product_line_form.html`, `product_variant_form.html` | Kit forms + danger/archive zone |
| `companies/forms.py` | Kit-friendly widgets (drop hand-written Bootstrap classes like CompanyForm) |
| `templates/companies/product_list.html`, `partials/product_package_actions.html` | Kit header/filter; hide line Delete; badge + archive colour fixes |
| `companies/forms.py` or new `ProductSearchForm` | Render-only filter fields for `rd_filter_card` |

---

### Task 1: Payment methods on the kit

**Files:**
- Modify: `templates/sales/payment_method_list.html`, `templates/sales/payment_method_form.html`, `sales/models.py`, `sales/views/payment_methods.py`, `sales/forms.py` (`PaymentMethodForm` widgets), `core/tests_ui_conventions.py`, `locale/ar/LC_MESSAGES/django.po`
- Test: `sales/tests_payment_methods_kit.py` (create)

**Interfaces:**
- Produces: `PaymentMethod.is_deletable` — `True` when no related `sales` and no related customer payments (both FKs PROTECT). List view annotates once (same pattern as `Company.has_sales_annotated` / `is_deletable`) so the list does not N+1.
- List: `rd_page_header` + New; table or mobile cards with name, Active badge, `row_actions` (omit `delete_url` when not deletable). Keep pagination. Optional: simple search later — out of scope unless already trivial.
- Form: `rd_page--form`, `rd_page_header` (back to list), `rd_fields`, Save/Cancel, danger zone like `company_form.html` (delete form if deletable; else `alert alert-info` with msgid: "This payment method can't be deleted because sales or customer payments still reference it. Turn off “Active” and save to hide it on the sales screen." → Arabic matching tone of the company sentence).

- [ ] **Step 1: Write failing tests** (`sales/tests_payment_methods_kit.py`): list hides delete URL for a method used by a sale; deletable method still has delete; edit page shows the explanation and no delete form when referenced; edit page shows Delete when unused; list/form use kit (`rd-page-head`); ProtectedError still returns the existing toast msgid when forced.
- [ ] **Step 2: Run** — expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `manage.py test sales companies core` — OK; `compilemessages -l ar`.
- [ ] **Step 5: Commit** `feat(sales): payment methods on the page kit; hide delete when referenced`

---

### Task 2: Product line and product variant forms on the kit

**Files:**
- Modify: `templates/companies/product_line_form.html`, `templates/companies/product_variant_form.html`, `companies/forms.py`, `core/tests_ui_conventions.py`, `locale/ar/LC_MESSAGES/django.po`
- Test: `companies/tests_product_forms_kit.py` (create)

**Interfaces:**
- Line form mirrors `company_form.html`: header back to product list, `rd_fields`, danger zone — deletable ⇒ delete confirm; else info note: "This product line can't be deleted because it has sales. Turn off “Active” and save to hide it from new sales."
- Variant form: header shows `{{ line.company.name }} — {{ line.name }}` as subtitle; `rd_fields`; zone: deletable ⇒ delete; elif active ⇒ Archive as `btn-outline-secondary` (not warning) + existing hint; else Restore + archived hint. Keep confirm texts.
- Strip hand-written Bootstrap classes from `ProductLineForm` / `ProductVariantForm` widgets (kit supplies them); keep `role=switch` on booleans if CompanyForm does.

- [ ] **Step 1: Failing tests** for kit markup, delete hidden + explanation, archive button class not `btn-outline-warning`, existing field names still post.
- [ ] **Step 2–4:** implement, test `companies core`, compilemessages.
- [ ] **Step 5: Commit** `feat(companies): product line and package forms on the page kit`

---

### Task 3: Product list on the kit

**Files:**
- Modify: `templates/companies/product_list.html`, `templates/companies/partials/product_package_actions.html`, `companies/views.py` (pass unbound filter form / `card_form` only if needed), `companies/forms.py` (`ProductSearchForm`: `company` ChoiceField optional, `q`, `show_archived` Boolean — same GET names), `core/tests_ui_conventions.py`, `locale/ar/LC_MESSAGES/django.po`
- Test: extend `companies/tests.py`

**Interfaces:**
- `rd_page_header` + New product line.
- `rd_filter_card` with `ProductSearchForm` (or keep the existing open/close partials if `rd_filter_card` cannot express the company select + checkbox cleanly — prefer kit; checkbox via `rd_field`).
- Keep company `<details class="rd-product-company" open>` and line `<details class="rd-product-line__details"{% if expand_packages %} open{% endif %}>` — tests assert the class and `open`.
- Replace `badge text-bg-secondary` "Sales default" with `rd-badge rd-badge--neutral`.
- Line Delete: omit when not `is_deletable` (no disabled button) — same owner rule as companies.
- Package Archive button: `btn-outline-secondary` (list + form already Task 2).
- Empty state via `partials/empty_state.html` if easy; keep "No product lines." text.
- Do not change the query/annotation logic in `product_list`.

- [ ] **Step 1: Failing tests:** `test_product_list_uses_kit`, `test_sales_default_is_rd_badge`, `test_archive_not_outline_warning`, `test_list_hides_delete_for_lines_with_sales`; existing filter/query-count/`open` tests stay green.
- [ ] **Step 2–4:** implement, browser 375+1280 light/dark screenshots to `.superpowers/companies-2026-10-16/`, full suite.
- [ ] **Step 5: Commit** `feat(companies): product list on the page kit; hide non-deletable line delete`

---

## Rulings to report

- Archive buttons are secondary (ink outline), not orange.
- Payment methods and product lines: Delete hidden when referenced/has sales; edit page explains Active off.
- company_detail deferred to the next batch.
