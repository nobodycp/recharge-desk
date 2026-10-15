# Companies Batch 1 — Layan/Sky Reconcile + Company List Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One clear reconcile page shared by Layan and Sky that puts "at supplier, missing in our system" and "in our system, missing at supplier" first, with quick actions, number search, a running state and plain-language errors; plus the company list/edit pages on the page kit.

**Architecture:** Both reconcile views keep their URLs, forms, adapters and template paths (`companies/layan_reconcile.html`, `companies/sky_reconcile.html` — the Sky path is asserted in `providers/tests/test_sky_reconcile.py:342`). The two page templates shrink to header + run form and include one shared results partial and one shared rows partial. Result rows get a provider-neutral `supplier_net` and results a `supplier_end_balance` so the shared partials never branch on provider for numbers. Tabs + search live in one small page script.

**Tech Stack:** Django 4.2 templates, page kit (`{% load kit %}`: `rd_page_header`, `rd_field(s)`, `rd_filter_card`, `rd_sort_link`, `partials/kit/row_actions.html`), htmx 1.9 hx-boost, Pearl tokens (`--rd-*`).

**Spec:** Owner answers 2026-10-15 recorded in `.superpowers/companies-inventory.md` ("Owner answers"), plus the code survey in the same file. Rules: `.cursor/rules/pearl-ui.mdc`, `.cursor/rules/ui-navigation-and-toasts.mdc`, `DESIGN.md`.

Owner answers, verbatim intent:
1. First look = BOTH "at supplier, not in our system" (with a quick way to record the sale) AND "recorded with us, absent at supplier", equal weight, top of the page.
2. No persistence — results shown once.
3. Hide "Matched" by default + search by number.
4. Sky estimated deficit must include settlements, like Layan.
5. Stay on the page with a "جاري الفحص…" state and a clear error (no background jobs).
6. Company list: hide Delete for companies with sales; explain the reason on the edit page.

## Global Constraints

- Never touch `db.sqlite3`. Browser checks use the probe server (`PYTHONPATH=.superpowers DJANGO_SETTINGS_MODULE=probe_settings .venv/bin/python manage.py runserver 127.0.0.1:8765`, users `admin` / `ياسمين`, password `pearl-probe-1`). Do not run a real Sky fetch from the probe; render results through tests or a stubbed adapter.
- Never remove displayed information, links or actions. Moving, rewording for clarity, restyling and regrouping are fine. Every number the current reconcile pages show (counts, section totals, absolute gap total, settlement total + hidden count, estimated deficit, row count, period, end-balance line, per-row reason, compare-lines details, activity breakdown, balance anomalies) must still be on the page.
- Keep: URLs, form field names, the literal button text `Run matching`, adapter `reconcile_template` values, the redirect/flash behaviour for unsupported companies.
- Colours only from `--rd-*` tokens; no raw hex; no `badge bg-*` / `text-bg-*`; no `table-warning/-info/-danger` row tints; status = `rd-badge rd-badge--paid|pending|cancelled|neutral` (+ text, never colour alone).
- Every user-visible string via `{% trans %}` / `gettext` with Arabic in `locale/ar/LC_MESSAGES/django.po`, then `.venv/bin/python manage.py compilemessages -l ar`.
- RTL first, logical properties; phone numbers and amounts in `dir="ltr"` + `.tabular-nums`.
- Touch targets ≥ 44px (3.45rem) below 992px; text ≥ 4.5:1; no readable text below 0.8rem; motion 150–450 ms transform/opacity only.
- Page scripts initialise on every boosted visit (`htmx.onLoad` or document-level delegation); no bind-once guards; JS feedback via `window.rdToast`.
- Messages via Django `messages` (they become toasts); no `alert-success/danger`; persistent notes may use `alert-info` / `alert-warning`.
- Shrink-only `LEGACY_*` sets in `core/tests_ui_conventions.py`: remove every file this plan migrates or deletes; never add one.
- Full suite: `.venv/bin/python manage.py test`. JS: `for f in tests/js/*.test.js; do node $f; done`.

## Review Focus

- Phone formats: rows carry normalised `05XXXXXXXX`, but users type `5XXXXXXXX`, `972…`, or with dashes/spaces. Search must match all of these; the quick-record link must prefill a valid 10-digit number (Task 3 `test_prefill_normalises_phone`, Task 4 JS test `search normalises input`).
- Sky slow or failing: a timeout, a login failure or a refused request must end in a readable Arabic toast with the dates still filled in — never a raw exception string and never a gateway timeout page (Task 2 error tests).
- Search with no matches, and clearing the search: show "no number matches" and restore the previous tab exactly (Task 4 JS tests).
- Boosted revisit and re-run: after leaving and returning, or running matching twice, tabs and search work once per click (no doubled handlers) (Task 4 browser check + JS test `init twice binds once per root`).
- Layan upload of a wrong file (CSV, image, other Excel): a friendly error toast, the other fields keep their values, the server log keeps the real exception (Task 2 `test_unreadable_layan_file_shows_friendly_error`).

---

## File Structure

| File | Responsibility |
|---|---|
| `templates/companies/layan_reconcile.html`, `sky_reconcile.html` (modify) | Page header + provider run form + include shared partials |
| `templates/companies/partials/reconcile_results.html` (create) | Summary, two action sections, search, tabs, panels, end-balance line |
| `templates/companies/partials/reconcile_rows.html` (create) | One section's rows (replaces `layan_reconcile_review_table.html` and `sky_reconcile_table.html`) |
| `templates/companies/partials/reconcile_waiting.html` (create) | First-run steps card for both providers |
| `static/js/reconcile.js` (create) | Tabs + number search for `[data-rd-reconcile]` |
| `tests/js/reconcile.test.js` (create) | JS unit tests (same harness style as `tests/js/ncheck_share.test.js`) |
| `static/css/design-system.css` (modify) | `/* Reconcile */` section; delete `.sky-rec-*` rules |
| `providers/adapters/{layan,sky}/reconcile.py` (modify) | `supplier_net`, `supplier_end_balance`; Sky deficit rule |
| `providers/adapters/sky/{adapter,client}.py` (modify) | Timeout code + bounded report read timeout |
| `companies/views.py` (modify) | Friendly errors; `provider`/`supplier_label` context; company list search form |
| `companies/forms.py` (modify) | `CompanySearchForm` (render-only `q`) |
| `sales/views/employee.py`, `templates/sales/employee_entry.html` (modify) | `?phone=` prefill + `from=reconcile` note |
| `templates/partials/management_nav.html` (modify) | Companies vs Products highlight |
| `templates/companies/company_list.html`, `partials/company_list_results.html`, `company_form.html` (modify) | Kit migration, hidden Delete, reason on edit page |

---

### Task 1: Cleanup and nav highlight

**Files:**
- Delete: `templates/reports/company_report.html`, `templates/companies/partials/layan_reconcile_table.html`, `templates/companies/partials/layan_reconcile_settlements.html`
- Modify: `core/tests_ui_conventions.py` (drop the three paths from every `LEGACY_*` set and fix the `# N files` counts), `templates/partials/management_nav.html:113-117`
- Test: `core/tests_shell.py`

**Interfaces:** Produces nothing later tasks use.

- [ ] **Step 1: Write the failing tests** in `core/tests_shell.py` (new class `CatalogNavHighlightTests`, logged-in management user, probe-free `TestCase` data: one company + `provider_key` not required because the connection/reconcile pages redirect for unsupported companies — so render the nav partial directly with `render_to_string("partials/management_nav.html", {"request": rf_request})` where `request.resolver_match` has `namespace="companies"` and `url_name` set):
  - `test_reconcile_and_connection_pages_highlight_companies`: for each of `layan_reconcile`, `sky_reconcile`, `company_connection_system`, `company_connection_services`, `company_detail`, `company_edit`: the Companies link (`href=reverse("companies:company_list")`) carries `is-active` and the Products link does not.
  - `test_product_pages_highlight_products`: for each of `product_list`, `product_line_create`, `product_line_edit`, `product_variant_create`, `product_variant_edit`: Products active, Companies not.
  - `test_dead_templates_are_gone`: the three deleted paths do not exist; `reports:company_report` still redirects (existing `reports/tests.py:767` keeps covering it — do not touch it).

- [ ] **Step 2: Run** `.venv/bin/python manage.py test core.tests_shell.CatalogNavHighlightTests` — expect FAIL (reconcile pages highlight Products; files exist).

- [ ] **Step 3: Implement.** In the nav, Products is active when `ns == 'companies' and 'product' in un`; Companies is active when `ns == 'companies' and 'product' not in un`. Delete the three templates and their `LEGACY_*` entries.

- [ ] **Step 4: Run** `.venv/bin/python manage.py test core reports companies` — expect OK.

- [ ] **Step 5: Commit** `fix(companies): nav highlights Companies on reconcile/connection pages; drop dead templates`

---

### Task 2: Reconcile behaviour — Sky deficit, timeouts, plain errors

**Files:**
- Modify: `providers/adapters/sky/reconcile.py:664-669`, `providers/adapters/sky/adapter.py:45-54`, `providers/adapters/sky/client.py:27-33,416-424,500-521`, `companies/views.py:143-256`, `locale/ar/LC_MESSAGES/django.po`
- Test: `providers/tests/test_sky_reconcile.py`, `providers/tests/test_layan_reconcile.py`, `companies/tests_reconcile_errors.py` (create)

**Interfaces:**
- Produces:
  - `SkyReconcileResult.estimated_deficit == total_not_recorded + sum(positive mismatch gaps) + total_split_settlements`.
  - `SkySalesClient.REPORT_READ_TIMEOUT_SECONDS = 40` (module constant `REPORT_READ_TIMEOUT_SECONDS`); `generic_api(api_name, wildcards, *, timeout=None)` and `_post(path, payload, *, timeout=None)` — `None` keeps `_request_timeout()`; `fetch_balance_report` passes `timeout=(LOGIN_CONNECT_TIMEOUT_SECONDS, REPORT_READ_TIMEOUT_SECONDS)`.
  - `_sky_errors` maps `requests.Timeout` → `TransientError("timeout")` (before the generic `RequestException` branch).

- [ ] **Step 1: Write the failing tests.**
  - `providers/tests/test_sky_reconcile.py`: change the settlement case at line ~125 to `self.assertEqual(result.estimated_deficit, Decimal("5"))` (charge 45, refund 40 ⇒ retained 5) and add `test_deficit_includes_settlements_and_positive_mismatch`: one not-recorded row (gap 45), one mismatch with gap +5, one settlement retaining 5 ⇒ `estimated_deficit == Decimal("55")` and `total_split_settlements == Decimal("5")`.
  - Same file, `test_report_fetch_uses_bounded_read_timeout`: patch `SkySalesClient._post` (or `http.post`) and assert `fetch_balance_report` sends `timeout == (LOGIN_CONNECT_TIMEOUT_SECONDS, 40)`.
  - Same file, `test_timeout_maps_to_timeout_code`: inside `_sky_errors()`, raising `requests.ReadTimeout()` surfaces `TransientError` with `str(exc) == "timeout"`.
  - `companies/tests_reconcile_errors.py` (management client; patch `companies.views.provider_service.reconcile`):
    - `test_sky_timeout_shows_plain_message`: side effect `TransientError("timeout")` ⇒ status 200, the messages contain `"Sky took too long to answer. Try a shorter period or run it again in a minute."` (assert on the English msgid with `translation.override("en")`), the response keeps `value="2026-08-01"` in `period_from`.
    - `test_sky_login_failure_shows_plain_message`: `AuthRequired("session_expired")` ⇒ `"Could not sign in to Sky. Check the connection settings on the company page."`.
    - `test_sky_connection_failure_shows_plain_message`: `TransientError("connection_error")` ⇒ `"Could not reach Sky. Check the internet connection and try again."`.
    - `test_sky_refusal_keeps_scrubbed_reason`: `PermanentError("bad period")` ⇒ `"Sky refused the request: bad period"`.
    - `test_unexpected_error_hides_exception_text`: `RuntimeError("secret-trace")` ⇒ message `"Matching failed unexpectedly. The details were saved in the server log."`, `"secret-trace"` not in any message, and `assertLogs("companies.views", "ERROR")` captures it.
    - `test_unreadable_layan_file_shows_friendly_error`: post a `SimpleUploadedFile("x.xlsx", b"not excel")` with dates and pending credits ⇒ message `"Could not read the file. Make sure it is the Layan charges report exported as Excel (.xlsx)."`, the response keeps the dates and the pending-credits text, the exception is logged at ERROR.
    - Unsupported-company redirects (`RECONCILE_NOT_SUPPORTED`) and the ImportError message keep their current behaviour (existing tests cover them; keep them green).

- [ ] **Step 2: Run** `.venv/bin/python manage.py test providers.tests.test_sky_reconcile companies.tests_reconcile_errors` — expect FAIL.

- [ ] **Step 3: Implement.**
  - Sky deficit: `estimated_deficit = total_not + deficit_mismatch + total_split`; update the comment above it ("settlements included per owner, 2026-10-15" is a history note — write the rule instead: "Retained settlement amounts count toward the deficit, as for Layan.").
  - Client/adapter timeouts per Interfaces. Login timeouts are unchanged.
  - Views: add `log = logging.getLogger(__name__)`. Sky: map `TransientError` code `timeout` / `sync_budget_exceeded` → timeout message; any `AuthRequired`, or a `TransientError`/`PermanentError` whose code classifies to `login_failed` via `providers.operations.classify_exception("reconcile", exc).error_code == "login_failed"` → login message; other `TransientError` → connection message; other `PermanentError` → refusal message with `%(error)s` = `str(exc)` (already scrubbed by the adapter); any other `Exception` → `log.exception(...)` + unexpected message. Layan: any non-ImportError, non-not-supported exception → `log.exception(...)` + unreadable-file message. Remove the now-unused msgids from `django.po` only if nothing else uses them.
  - Arabic (`django.po`):
    - "Sky took too long to answer. Try a shorter period or run it again in a minute." → "سكاي تأخّر بالرد. جرّب فترة أقصر أو أعد المحاولة بعد دقيقة."
    - "Could not sign in to Sky. Check the connection settings on the company page." → "تعذّر تسجيل الدخول إلى سكاي. افحص إعدادات الربط من صفحة الشركة."
    - "Could not reach Sky. Check the internet connection and try again." → "تعذّر الوصول إلى سكاي. افحص الاتصال بالإنترنت وحاول مرة أخرى."
    - "Sky refused the request: %(error)s" → "رفض سكاي الطلب: %(error)s"
    - "Matching failed unexpectedly. The details were saved in the server log." → "فشلت المطابقة بشكل غير متوقع. تم حفظ التفاصيل في سجل الخادم."
    - "Could not read the file. Make sure it is the Layan charges report exported as Excel (.xlsx)." → "تعذّرت قراءة الملف. تأكد أنه تقرير شحنات ليان بصيغة Excel ‏(.xlsx)."

- [ ] **Step 4: Run** `.venv/bin/python manage.py test providers companies` and `compilemessages -l ar` — expect OK.

- [ ] **Step 5: Commit** `feat(reconcile): Sky deficit counts settlements; bounded Sky report fetch; plain error messages`

---

### Task 3: Quick-record link target (sale entry prefill)

**Files:**
- Modify: `sales/views/employee.py:101-112`, `templates/sales/employee_entry.html`, `locale/ar/LC_MESSAGES/django.po`
- Test: `sales/tests_entry_prefill.py` (create)

**Interfaces:**
- Produces: `GET {% url 'sales:employee_entry' %}?company=<pk>&phone=<digits>&from=reconcile` opens the entry form with `company` selected (existing behaviour), `reference_number` prefilled with `normalize_sale_phone(phone)` (only when the result matches `SALE_PHONE_RE`; otherwise not prefilled), and — only when `from=reconcile` — an `alert-warning` note above the form. Context key `from_reconcile: bool`. Task 4 builds this URL.

- [ ] **Step 1: Write the failing tests** (`sales/tests_entry_prefill.py`, management user — `employee_required` admits management):
  - `test_prefill_normalises_phone`: `?company=<pk>&phone=599123456` ⇒ the reference input has `value="0599123456"`; `?phone=972599123456` ⇒ same; `?phone=059-912-3456` ⇒ same.
  - `test_invalid_phone_is_not_prefilled`: `?phone=12345` ⇒ no `value="12345"` on the reference input and no error rendered.
  - `test_reconcile_note_only_when_from_reconcile`: with `from=reconcile` the page contains the English msgid "This number was already charged at the supplier. Record the sale without charging it again." (under `translation.override("en")`); without it, not.
  - `test_post_ignores_get_phone`: a POST still uses only POST data (no prefill side effects).

- [ ] **Step 2: Run** `.venv/bin/python manage.py test sales.tests_entry_prefill` — expect FAIL.

- [ ] **Step 3: Implement** in `employee_entry` (GET only); note markup `<div class="alert alert-warning" role="note">` with the translated sentence; Arabic: "هذا الرقم مشحون عند المزوّد مسبقاً. سجّل البيعة بدون شحنه مرة ثانية."

- [ ] **Step 4: Run** `.venv/bin/python manage.py test sales` — expect OK (the 2 number-check happy-path tests are date-independent after the number-check branch is merged; if this branch was cut before that merge, they may fail on this date only — report, don't fix here).

- [ ] **Step 5: Commit** `feat(sales): sale entry accepts a phone prefill and a reconcile note`

---

### Task 4: Shared reconcile results (both pages)

**Files:**
- Create: `templates/companies/partials/reconcile_results.html`, `templates/companies/partials/reconcile_rows.html`, `templates/companies/partials/reconcile_waiting.html`, `static/js/reconcile.js`, `tests/js/reconcile.test.js`
- Modify: `templates/companies/layan_reconcile.html`, `templates/companies/sky_reconcile.html`, `templates/companies/partials/layan_reconcile_activity_detail.html` (token-coloured amounts only), `providers/adapters/layan/reconcile.py`, `providers/adapters/sky/reconcile.py`, `companies/views.py` (context), `static/css/design-system.css`, `core/tests_ui_conventions.py`, `DESIGN.md` (one short "Reconcile page" note), `locale/ar/LC_MESSAGES/django.po`
- Delete: `templates/companies/partials/layan_reconcile_review_table.html`, `templates/companies/partials/sky_reconcile_table.html`
- Test: `companies/tests_reconcile_page.py` (create)

**Interfaces:**
- Consumes: Task 2 messages and deficit; Task 3 URL contract.
- Produces:
  - `ReconcilePhoneRow.supplier_net` (→ `layan_net`) and `SkyReconcilePhoneRow.supplier_net` (→ `sky_net`), read-only properties.
  - `LayanReconcileResult.supplier_end_balance` (→ `layan_end_balance`) and `SkyReconcileResult.supplier_end_balance` (→ `sky_end_balance`).
  - View context adds `provider` (`"layan"` | `"sky"`) and `supplier_label` (`_("Layan")` | `_("Sky")`).
  - `{% include "companies/partials/reconcile_rows.html" with rows=… section="missing|extra|other|mismatch|settlement|matched" %}`.
  - Root `<div data-rd-reconcile>`; tabs `[data-rd-rec-tab="<panel>"]` with `role="tab"`, `aria-controls`, `aria-selected`; panels `[data-rd-rec-panel="<panel>"]` with `role="tabpanel"`; rows `[data-rd-rec-row]` with `data-phone="<normalised digits>"`; groups `[data-rd-rec-group]`; search `input[data-rd-rec-search]`; live count `[data-rd-rec-count]` (`aria-live="polite"`); no-match `[data-rd-rec-empty]`.
  - `static/js/reconcile.js` exports for tests (CommonJS guard like `number-check-share.js`): `normalizeQuery(text) -> string` (digits only; `972…` → `0…`; 9-digit `5…` → `05…`; partial input stays as typed digits) and `initReconcile(root)`.

**Page layout (top to bottom), both providers:**
1. `rd_page_header`: title (`Layan report matching` / `Sky report matching`), subtitle company name, back link `Back to supplier`.
2. Run card (`rd-card`): provider fields via `rd_field` (Layan: dates, minimum difference, file, pending credits full-width; Sky: dates, minimum difference + its help), submit `btn-primary` "Run matching". A `role="status"` block shown only while `form[data-rd-submitting]` (set by `static/js/nav.js` on boosted submits): spinner + "جاري الفحص…" (msgid "Checking…") and, for Sky, "Sky can take up to a minute." (→ "قد يستغرق سكاي حتى دقيقة."). Pure CSS show/hide.
3. No result ⇒ `reconcile_waiting.html` (three numbered steps; Sky keeps its current three step texts; Layan: "Upload the Layan charges report" / "Run matching" / "Start with the two lists at the top").
4. Result ⇒ `reconcile_results.html`:
   - Meta line: `{{ row_count }} report rows` (Layan) / `Sky rows` (Sky) · period.
   - Summary strip, four `rd-stat`: **At {supplier}, not in system** (count = `not_recorded|length + logged_other_supplier|length`, total = `total_not_recorded + total_logged_other_supplier`), **In system, not at {supplier}** (`rd_only|length`, `total_rd_only`), **Cost differences** (`mismatch_count`, `total_amount_mismatch`), **Estimated deficit** (`estimated_deficit`, danger tone) with its rule under it — Layan: "Missing sales + retained settlements" (→ "المبيعات الناقصة + المبالغ المحتجزة من التسويات"); Sky: "Missing sales + higher supplier costs + retained settlements" (→ "المبيعات الناقصة + فروقات التكلفة الأعلى عند المزوّد + المبالغ المحتجزة من التسويات").
   - Two equal action sections side by side from 992px, stacked below (each an `rd-card` with heading, one-line explanation, section total, empty text "Nothing here." → "لا شيء هنا."):
     - **At {supplier}, not in system** — `not_recorded` rows, then a sub-group "Logged under another supplier" for `logged_other_supplier`. Each row's action: link "Record sale" (→ "سجّل البيعة") to `{% url 'sales:employee_entry' %}?company={{ company.pk }}&phone={{ row.phone }}&from=reconcile` (`hx-boost="false"`, opens in the same tab).
     - **In system, not at {supplier}** — `rd_only` rows. Each row's action: link "Find sale" (→ "اعرض البيعة") to `{% url 'sales:management_sale_list' %}?q={{ row.phone }}`.
   - Search: `type="search"`, `inputmode="numeric"`, label "Search by number" (→ "ابحث بالرقم"), placeholder `05XXXXXXXX`.
   - Tabs (`.rd-section-tabs`): **Cost differences** (default selected; badge `rd-badge--pending` count), **Settlements** (badge `rd-badge--neutral` = `info_count`; Sky panel also holds Balance anomalies), **Matched** (badge `rd-badge--paid`). Panels keep every current note and total: Layan hidden-settlements sentence, "Section total", "Absolute gap total", the settlement explanation reworded to "Settlements are charge + disconnect cycles. The retained amount counts toward the estimated deficit." (→ "التسويات هي دورات شحن ثم فصل. المبلغ المحتجز منها يُحسب ضمن العجز التقديري."), compare-lines hint.
   - End-balance line: "Balance at report end: system {{ rd_balance_end }} · {supplier} {{ supplier_end_balance }} · gap {{ balance_gap }}" (replaces the unexplained "RD"); Layan keeps "End balance not found in file." when `supplier_end_balance is None`; Sky shows nothing in that case (as today).
5. `reconcile_rows.html` row content (every current field kept): phone (`dir="ltr"`, mono), `category_label` as `rd-badge rd-badge--neutral`, `reason`, the `<details>` compare block (supplier lines, `activity_detail` include, system sales with eSIM `rd-badge--neutral`), supplier amount (`row.supplier_net`), system amount (`row.rd_net`), gap with sign **and** a word: gap > 0 "Supplier higher" (→ "المزوّد أعلى"), gap < 0 "System higher" (→ "النظام أعلى"). Row accents via `.rd-rec-row--missing|--extra|--higher|--lower` token classes (no Bootstrap table tints, no inline styles). Below 768px rows render as stacked cards (no horizontal page overflow at 375px).

**Search behaviour (`initReconcile`):** query = `normalizeQuery(input.value)`. Empty ⇒ restore the tab state that was active before searching. Non-empty ⇒ every panel and both action sections visible, rows whose `data-phone` contains the query (also try the query with a leading `0` stripped/added so `599…` finds `0599…`) shown, others hidden, groups with no visible row hidden, tabs marked inert (`aria-disabled="true"`), live count "N numbers match" / no-match block "No number matches." (→ "%(n)s رقم مطابق" via a `data-` template string rendered by Django with `blocktrans count`; "لا يوجد رقم مطابق."). Tabs: click or Left/Right arrows switch panels; `aria-selected`, `tabindex` roving. Bind per root on `htmx.onLoad`, marking the root `data-rd-rec-ready` so a second `onLoad` on the same root does not rebind (this is per-element state, not a global guard).

- [ ] **Step 1: Write the failing Python tests** (`companies/tests_reconcile_page.py`; patch `companies.views.provider_service.reconcile` to return a hand-built Layan/Sky result object with at least one row in every bucket, and the right template path):
  - `test_both_action_sections_come_before_tabs`: in the HTML, the index of the "At Layan, not in system" heading and of "In system, not at Layan" are both lower than the index of `data-rd-rec-tab`.
  - `test_record_sale_link_for_missing_rows`: contains `reverse("sales:employee_entry") + "?company=<pk>&amp;phone=0599000001&amp;from=reconcile"`.
  - `test_find_sale_link_for_system_only_rows`: contains `reverse("sales:management_sale_list") + "?q=0599000002"`.
  - `test_matched_panel_hidden_by_default`: the `data-rd-rec-panel="matched"` element has `hidden`; the cost-differences tab has `aria-selected="true"`.
  - `test_every_number_still_shown`: for the stub result, each of `row_count`, `total_not_recorded`, `total_rd_only`, `total_amount_mismatch`, `total_split_settlements`, `split_settlements_hidden_count` (Layan), `estimated_deficit`, `balance_gap`, `rd_balance_end`, `supplier_end_balance` appears formatted (`floatformat:2`).
  - `test_gap_has_text_not_only_colour`: a positive-gap row renders "Supplier higher"; a negative one "System higher".
  - `test_sky_settlement_text_matches_new_rule`: Sky page no longer contains "they do not increase estimated deficit" nor "Settlements (not in deficit)"; contains the new explanation.
  - `test_waiting_card_on_first_visit` for both providers (GET, no result) and `test_running_status_markup_present` (`role="status"` block with "Checking…").
  - `test_sky_template_path_unchanged`: existing `FetchSkyRowsTests` stays green; `Run matching` literal present on both pages.
  - Rows/result property tests in `providers/tests/test_layan_reconcile.py` / `test_sky_reconcile.py`: `supplier_net` and `supplier_end_balance` return the provider values.

- [ ] **Step 2: Write the failing JS tests** (`tests/js/reconcile.test.js`, minimal DOM stub like `tests/js/ncheck_share.test.js`): `normalizeQuery` cases (`"599 123"`→`"599123"`, `"972599123456"`→`"0599123456"`, `"599123456"`→`"0599123456"`, `"abc"`→`""`); `search normalises input` (typing `972599000001` shows the `0599000001` row); `search shows matched rows and hides empty groups`; `clearing search restores previous tab`; `no match shows empty block`; `arrow keys move tabs`; `init twice binds once per root`.

- [ ] **Step 3: Run** both — expect FAIL.

- [ ] **Step 4: Implement** per the layout above. Remove the inline `<script>` blocks from both pages; include `static/js/reconcile.js` with `{% static %}` in the page (`{% block scripts %}` or the pattern used by `employee_refresh_number.html`). CSS: new `/* Reconcile */` section using tokens only, scoped under `.rd-rec` (`.rd-rec-actions`, `.rd-rec-row*`, `.rd-rec-running`, …); delete `.sky-rec-run__*` and `.sky-rec-waiting*` (design-system.css ~2738-2848) after confirming no other user. `LEGACY_*`: remove `layan_reconcile.html` and `sky_reconcile.html` from every set they are in, and the two deleted partials.

- [ ] **Step 5: Run** `.venv/bin/python manage.py test companies providers core`, `node tests/js/reconcile.test.js`, `compilemessages -l ar` — expect OK.

- [ ] **Step 6: Browser check** (probe server; feed a result by temporarily pointing `provider_service.reconcile` at a stub via a probe-only setting or by rendering the template through a test that writes HTML to `.superpowers/companies-2026-10-15/` — never a live Sky call): 375 light + dark and 1280 light: action sections first, record/find links, search `599000001` shows the row from any bucket, clearing restores the tab, matched hidden by default, no horizontal overflow, 44px targets, running status visible during a slow submit (stub sleeps 2 s). Save screenshots to `.superpowers/companies-2026-10-15/`.

- [ ] **Step 7: Commit** `feat(companies): one reconcile page for Layan and Sky — action lists first, search, running state`

---

### Task 5: Company list and edit page on the kit

**Files:**
- Modify: `templates/companies/company_list.html`, `templates/companies/partials/company_list_results.html`, `templates/companies/company_form.html`, `companies/forms.py` (`CompanySearchForm`, plus kit-friendly widget attrs on `CompanyForm` if needed), `companies/views.py` (`company_list` passes `card_form = CompanySearchForm(initial=request.GET.dict())`), `core/tests_ui_conventions.py`, `locale/ar/LC_MESSAGES/django.po`
- Test: `companies/tests.py`

**Interfaces:**
- Consumes: kit tags (see `templates/accounts/user_list.html` and `templates/accounts/partials/user_list_results.html` for the reference pattern).
- Produces: `CompanySearchForm` — render-only `forms.Form` with `q = CharField(required=False, label=_("Search"))`, placeholder "Company name or notes…"; the view's filtering logic is unchanged.

- [ ] **Step 1: Write the failing tests** (`companies/tests.py`):
  - `test_list_hides_delete_for_companies_with_sales`: a company with a sale has no `companies:company_delete` URL anywhere in the list HTML (desktop and mobile), and no disabled Delete button; a company without sales still has the delete form with the confirm text.
  - `test_edit_page_explains_why_delete_is_unavailable`: the edit page of a company with sales contains "This company can't be deleted because it has sales. To hide it from the sales screen, turn off “Active” and save." (English msgid under `override("en")`) and no delete form; a company without sales still shows "Delete company".
  - `test_company_list_uses_kit`: list HTML contains `rd-page-head`, the search field `name="q"` inside the filter card, sort links for Name / Opening / Current balance / Active, and keeps `id="company-list-results"` with `hx-boost="true"` (existing `test_company_list_results_container_is_hx_boosted` stays green), and the existing query-count test stays green.
  - `test_company_form_uses_kit`: edit page has `rd-page-head` and `rd-field` wrappers; posting with an error keeps typed values.

- [ ] **Step 2: Run** `.venv/bin/python manage.py test companies.tests` — expect FAIL.

- [ ] **Step 3: Implement.** List: `rd_page_header` (title, subtitle, primary "New company"), `rd_filter_card` with `CompanySearchForm`, `rd_sort_link` headers, `partials/kit/row_actions.html` with `delete_url` only when `c.is_deletable` (Details stays as its own link). Edit page: `rd_page_header` with back link to the list, `rd_fields form`, Save/Cancel, then the delete zone: deletable ⇒ the existing confirm form; not deletable ⇒ `<p class="alert alert-info" role="note">` with the sentence above (Arabic: "لا يمكن حذف هذه الشركة لأن عليها مبيعات. لإخفائها من شاشة البيع، ألغِ تفعيل «نشطة» واحفظ."). Remove `company_list.html` / `company_form.html` from every `LEGACY_*` set; drop the inline `max-width` (kit width class instead).

- [ ] **Step 4: Run** `.venv/bin/python manage.py test companies core` — expect OK. Browser: 375 + 1280 light/dark, list and edit page, no overflow, 44px targets; screenshots to `.superpowers/companies-2026-10-15/`.

- [ ] **Step 5: Commit** `feat(companies): company list and edit page on the page kit; hide delete for companies with sales`

---

## Rulings to report to the owner

- Sky deficit now = missing sales + higher supplier costs + retained settlements (Layan keeps missing sales + retained settlements; the positive cost differences were already in Sky's figure and stay).
- "Record sale" opens the normal sale entry with the number and company filled in and a note not to charge again; nothing is recorded automatically.
- Sky's report fetch now stops after 40 s with a clear message instead of risking a gateway timeout; a Sky login that is needed mid-run can still take longer (rare: the session is kept alive in the background).
