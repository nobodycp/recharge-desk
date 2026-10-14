# Company ↔ Provider Connection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move each company's provider connection (account, login test, per-service switches) out of phone refresh and onto the company page, with one `service_state` gate used by refresh, charge and reconcile.

**Architecture:** `providers.ProviderAccount` becomes `ProviderConnection` (same table) with three service switches; `Company.phone_refresh_provider` becomes `Company.provider_key` (same column). `providers.service.service_state(company, capability)` is the single gate. The company page gets a "connection" tab (HTMX partial like the existing tabs); the phone-refresh providers page keeps only refresh rules and the number test; the dashboard gets one health alert.

**Tech Stack:** Django 4.2, Celery + Redis, HTMX, Bootstrap (existing `rd-*` styles).

**Spec:** `docs/superpowers/specs/2026-10-14-company-provider-connection-design.md`

## Global Constraints

- No DB table or column renames: `ProviderConnection.Meta.db_table = "providers_provideraccount"`; `Company.provider_key` has `db_column="phone_refresh_provider"`.
- `phone_refresh.ProviderConfig` model/table stays in this release; **no code reads or writes it** after Task 1 (drop is a later release).
- Switch defaults: `refresh_enabled=True` (then copied from `ProviderConfig.is_enabled`), `charge_enabled=False`, `reconcile_enabled=True`.
- Never run a provider login inside a web request; the login test goes through the worker.
- Secrets stay write-only in every form (blank field keeps the stored value).
- UI copy is Arabic; service labels exactly: "تحديث الأرقام", "الشحن", "مطابقة الحسابات"; tab label "الربط مع المزوّد"; system label "نظام الشركة"; button "اختبار تسجيل الدخول".
- All connection views are `@management_required`.
- Run tests with `.venv/bin/python manage.py test <label>`; the full suite must stay green, plus `makemigrations --check --dry-run` clean.
- Never `git add -A`/`.`; stage explicit paths. Never push (Task 8 pushes only after the user's go-ahead given in this conversation).

## Review Focus

- A company whose system needs no login (Aloha, Areen): no account form, services can still be active, no "not configured" state.
- Two companies set to the same system share one connection: the tab must say so ("مشترك مع: …") so a switch change is not a surprise.
- Pressing "test login" twice while a test is pending must not enqueue a second login.
- Stored secrets that cannot be decrypted: state `not_configured` with the existing re-enter warning, never a 500.
- Refresh switch off: customers get `provider_off`, but the admin number test (`bypass_provider_off=True`) still reaches the provider.

---

### Task 1: `ProviderConnection` model + switches + data migration

**Files:**
- Modify: `providers/models.py` (rename class, add fields, `for_key`)
- Create: `providers/migrations/0004_provider_connection.py`
- Modify (rename references only): `providers/accounts.py`, `providers/health.py`, `providers/tasks.py`, `providers/forms.py`, `sales/charging.py`, `phone_refresh/views/settings.py`, `README.md`, tests in `providers/tests/`, `sales/tests_charging.py`
- Test: `providers/tests/test_connection_model.py`

**Interfaces:**
- Produces: `providers.models.ProviderConnection` with fields `refresh_enabled: bool`, `charge_enabled: bool`, `reconcile_enabled: bool` (+ all existing `ProviderAccount` fields); `ProviderConnection.for_key(provider_key: str) -> ProviderConnection` (get-or-create, new rows `is_enabled=True`); `ProviderConnection.SERVICE_FIELDS: dict[Capability, str]` = `{REFRESH: "refresh_enabled", CHARGE: "charge_enabled", RECONCILE: "reconcile_enabled"}`.

- [ ] **Step 1: Write failing tests** in `providers/tests/test_connection_model.py`:
  - `test_for_key_creates_enabled_row_with_defaults`: `ProviderConnection.for_key("aloha")` → `is_enabled is True`, `refresh_enabled is True`, `charge_enabled is False`, `reconcile_enabled is True`; second call returns the same pk.
  - `test_table_name_unchanged`: `ProviderConnection._meta.db_table == "providers_provideraccount"`.
  - `test_migration_copies_refresh_toggle` (use `django.db.migrations.executor.MigrationExecutor`: migrate providers to `0003_providerjob`, create `phone_refresh.ProviderConfig(provider="layan", is_enabled=False)` and `(provider="sky", is_enabled=True)` via historical apps, migrate to `0004_provider_connection`) → connection `layan.refresh_enabled is False`, `sky.refresh_enabled is True`; keys without a `ProviderConfig` row get no new row.
- [ ] **Step 2: Run** `.venv/bin/python manage.py test providers.tests.test_connection_model` → FAIL (`ImportError: ProviderConnection`).
- [ ] **Step 3: Implement.** In `providers/models.py` rename `ProviderAccount` → `ProviderConnection`, add `Meta.db_table = "providers_provideraccount"`, the three `BooleanField`s with the defaults above, `SERVICE_FIELDS`, and `for_key` (`get_or_create(provider_key=key)`). Migration `0004_provider_connection` (dependencies: `("providers", "0003_providerjob")`, `("phone_refresh", "0020_apitoken_scopes")`), operations in order: `AlterModelTable("provideraccount", "providers_provideraccount")`, `RenameModel("ProviderAccount", "ProviderConnection")`, three `AddField`s, `RunPython(copy_refresh_toggles, noop)` where `copy_refresh_toggles` does, for each `ProviderConfig` row, `ProviderConnection.objects.get_or_create(provider_key=row.provider)` then sets `refresh_enabled=row.is_enabled`. Replace every `ProviderAccount` reference in the listed files (class name, imports, docstrings; the form class keeps the name `ProviderAccountForm`).
- [ ] **Step 4: Run** the new tests, then `.venv/bin/python manage.py test providers sales` → PASS; `.venv/bin/python manage.py makemigrations --check --dry-run` → "No changes detected".
- [ ] **Step 5: Commit**
```bash
git add providers/models.py providers/migrations/0004_provider_connection.py providers/accounts.py providers/health.py providers/tasks.py providers/forms.py providers/tests sales/charging.py sales/tests_charging.py phone_refresh/views/settings.py README.md
git commit -m "feat(providers): ProviderConnection with per-service switches"
```

### Task 2: `Company.provider_key` rename + remove the obsolete mapping report

**Files:**
- Modify: `companies/models.py`, `companies/forms.py`, `sales/charging.py`, `providers/service.py`, `phone_refresh/services/refresh_service.py:86`, tests using `phone_refresh_provider=` (`sales/tests.py`, `sales/tests_charge_ui.py`, `sales/tests_charging.py`, `providers/tests/test_provider_mapping.py`, `providers/tests/test_sky_reconcile.py`, `providers/tests/test_layan_reconcile.py`), `DEPLOY_COOLIFY.md`
- Create: `companies/migrations/0014_company_provider_key.py`
- Delete: `providers/management/commands/providers_mapping_report.py`, `providers/name_rules.py` and their tests (migration `companies 0012` is applied in production; the report has no further use). Remove the "Pre-deploy check" section from `DEPLOY_COOLIFY.md`.
- Test: `companies/tests.py`

**Interfaces:**
- Produces: `Company.provider_key` (CharField, same choices, verbose name `_("company system")`, Arabic translation "نظام الشركة" in `locale/ar/LC_MESSAGES/django.po` + compiled `.mo`).

- [ ] **Step 1: Failing test** in `companies/tests.py`: `test_provider_key_uses_legacy_column` → `Company._meta.get_field("provider_key").column == "phone_refresh_provider"`; `test_company_form_has_provider_key` → `"provider_key" in CompanyForm().fields`.
- [ ] **Step 2: Run** `.venv/bin/python manage.py test companies` → FAIL.
- [ ] **Step 3: Implement.** Migration `0014_company_provider_key` (depends on `0013_phone_refresh_provider_help_text`): `AlterField` adding `db_column="phone_refresh_provider"`, then `RenameField("company", "phone_refresh_provider", "provider_key")`. Rename every non-migration reference (the grep `rg -n "phone_refresh_provider" --glob '!**/migrations/**' --glob '!docs/**'` must return nothing afterwards). If `rg -n "name_rules" --glob '!**/migrations/**'` shows other users besides the report and its tests, keep `name_rules.py` and only delete the command.
- [ ] **Step 4: Run** full suite `.venv/bin/python manage.py test` → PASS; `makemigrations --check --dry-run` clean; `compilemessages -l ar` succeeds.
- [ ] **Step 5: Commit** the listed paths: `git commit -m "refactor(companies): phone_refresh_provider -> provider_key (same column)"`.

### Task 3: `service_state` gate used by refresh, charge, reconcile

**Files:**
- Modify: `providers/service.py` (add `ServiceState`, `service_enabled`, `service_state`; delete `company_supports`), `providers/service.py:192` (reconcile), `companies/views.py:89`, `companies/statement.py:16,292-297`, `sales/charging.py:46-53`, `phone_refresh/services/refresh_service.py:330` (and its `ProviderConfig` import), `phone_refresh/tests_internal_test_screens.py:261-263`, `providers/tests/test_provider_mapping.py`
- Test: `providers/tests/test_service_state.py`

**Interfaces:**
- Consumes: `ProviderConnection.for_key`, `SERVICE_FIELDS` (Task 1); `Company.provider_key` (Task 2).
- Produces:
  - `ServiceState` frozen dataclass: `code: str` (one of `"active" | "off" | "unsupported" | "not_configured"`), `reason: str` (Arabic, empty when active).
  - `service_enabled(provider_key: str, capability: Capability) -> bool` — connection exists-or-default: master `is_enabled` and the capability's switch (`check_charge` follows `charge_enabled`).
  - `service_state(company, capability: Capability, provider: str | None = None) -> ServiceState` — order: no/unknown `provider_key` or `provider` mismatch → `unsupported` "لم يتم تحديد نظام الشركة"/"غير متاح لهذا النظام بعد"; capability not in adapter → `unsupported` "غير متاح لهذا النظام بعد"; adapter `needs_login` and `accounts.get_credentials` raises `PermanentError("config_error")` → `not_configured` "بيانات الحساب ناقصة أو غير مقروءة"; master off or switch off → `off` "موقوفة"; else `active`.

- [ ] **Step 1: Failing tests** in `providers/tests/test_service_state.py`: one test per row of the matrix — no system; unknown key; aloha + CHARGE (unsupported); sky without credentials (not_configured, no exception even with an undecryptable blob — set `secret_encrypted="garbage"`); master off; `reconcile_enabled=False`; active sky RECONCILE with saved credentials; aloha REFRESH active with no credentials. Plus: `test_refresh_switch_off_returns_provider_off` (refresh_phone for a mapped sky number with `refresh_enabled=False` → status `provider_off`) and `test_admin_bypass_ignores_refresh_switch` (`bypass_provider_off=True` reaches the adapter — mock the adapter call). Plus `test_can_charge_needs_charge_switch` in `sales/tests_charging.py` (adapter declaring CHARGE patched in; `charge_enabled=False` → `can_charge` False, True → True).
- [ ] **Step 2: Run** `.venv/bin/python manage.py test providers.tests.test_service_state` → FAIL.
- [ ] **Step 3: Implement** the interfaces above. Refresh: replace `ProviderConfig.is_provider_enabled(provider_key)` with `service_enabled(provider_key, Capability.REFRESH)`. Charge: `can_charge(company) = service_state(company, Capability.CHARGE).code == "active"`. Reconcile (`service.reconcile`, `companies/views.py` guard, `statement.py` flags): `service_state(company, Capability.RECONCILE, provider=...).code == "active"`. After this task `rg -n "ProviderConfig|company_supports" --glob '!**/migrations/**' --glob '!docs/**' providers sales companies phone_refresh/services` returns nothing.
- [ ] **Step 4: Run** full suite → PASS.
- [ ] **Step 5: Commit** listed paths: `git commit -m "feat(providers): service_state gate for refresh, charge and reconcile"`.

### Task 4: Login test through the worker + session reset on save

**Files:**
- Modify: `providers/sessions.py` (add `reset_session`), `providers/tasks.py` (add `test_connection`), `providers/forms.py` (`ProviderAccountForm`: drop `is_enabled` field; `save()` calls `reset_session`)
- Create: `providers/login_test.py`
- Test: `providers/tests/test_login_test.py`

**Interfaces:**
- Produces:
  - `sessions.reset_session(provider_key: str) -> None` — clears the stored session (`SessionStore(key).clear()`) and deletes the cool-down and failure-count keys.
  - `tasks.test_connection(provider_key: str) -> None` (Celery, queue `refresh`) — `reset_session` then `service.keepalive(key)`; never raises (log scrubbed error; `record_login` already stores the result).
  - `login_test.start(provider_key: str) -> str` — returns `"started"`, `"pending"` (a test started < 150 s ago and `last_login_at` is older than it) or `"unavailable"` (enqueue raised); start time stored in Django cache key `providers:login-test:<key>` for 150 s.
  - `login_test.status(provider_key: str) -> dict` — `{"pending": bool, "last_login_at", "last_login_ok", "last_error"}`; `pending` is true while the cache key exists and `last_login_at` is missing or older than it.

- [ ] **Step 1: Failing tests:** `test_start_enqueues_once` (mock `test_connection.delay`; two `start()` calls → second returns `"pending"`, `delay` called once); `test_start_unavailable_when_enqueue_fails` (`delay` raises → `"unavailable"`, cache key not set); `test_status_pending_until_login_recorded` (start, then `record_login(key, True)` → `pending` False, `last_login_ok` True); `test_task_resets_then_keepalive` (mock `service.keepalive`; a saved session in the store is gone after the task); `test_form_save_resets_session`; `test_form_has_no_is_enabled_field`.
- [ ] **Step 2: Run** `.venv/bin/python manage.py test providers.tests.test_login_test` → FAIL.
- [ ] **Step 3: Implement** the interfaces. `start` never calls the adapter itself.
- [ ] **Step 4: Run** `providers` tests → PASS.
- [ ] **Step 5: Commit:** `git commit -m "feat(providers): login test via worker; reset session on account save"`.

### Task 5: Company page "الربط مع المزوّد" tab

**Files:**
- Modify: `companies/statement.py` (`TAB_CONNECTION = "connection"`, accept it in `active_company_statement_tab`, context + HTMX partial branch), `templates/companies/company_detail.html` (4th tab + include), `companies/urls.py`, `companies/views.py`
- Create: `companies/connection.py` (context builder + POST handlers), `companies/forms_connection.py` (`CompanySystemForm`, `ConnectionServicesForm`), `templates/companies/partials/company_detail_connection.html`, `templates/companies/partials/connection_login_status.html`
- Test: `companies/tests_connection.py`

**Interfaces:**
- Consumes: Tasks 1–4 (`ProviderConnection.for_key`, `service_state`, `ProviderAccountForm`, `login_test.start/status`, `providers.health.status()`).
- Produces URLs (namespace `companies`, all POST except status):
  - `company_connection_system` → `management/companies/<int:pk>/connection/system/` (saves `provider_key`)
  - `company_connection_services` → `.../connection/services/` (`is_enabled`, `refresh_enabled`, `charge_enabled`, `reconcile_enabled`; switches of unsupported services are ignored, never stored as on)
  - `company_connection_account` → `.../connection/account/` (`ProviderAccountForm(provider_key=company.provider_key)`; 404 if the adapter does not `needs_login`)
  - `company_connection_test` → `.../connection/test-login/` (HTMX; returns `connection_login_status.html`)
  - `company_connection_status` → GET `.../connection/status/` (HTMX poll target; the partial carries `hx-get` + `hx-trigger="every 2s"` only while `pending`)
  - Non-HTMX POSTs redirect to `company_detail?tab=connection` with a flash message.
- Tab content (top to bottom): system select; if a system is set — services form (master switch + one row per service with switch and `ServiceState` badge/reason; reconcile row links to the existing reconcile page when active); account card + test button + login status (only when `needs_login`); "مشترك مع: <names>" when other companies share the `provider_key`; one line "Redis ✔/✖ — Worker ✔/✖".

- [ ] **Step 1: Failing tests** in `companies/tests_connection.py`: employee GET of `?tab=connection` → redirected/403; management sees the tab label "الربط مع المزوّد"; posting system `layan` sets `provider_key`; services POST turns `refresh_enabled` off; services POST with `charge_enabled=on` for a system without CHARGE leaves it False; account POST for aloha → 404; account POST for sky saves username (secrets write-only: response never contains the saved password); test-login POST calls `login_test.start` (mocked) and renders the pending partial with `every 2s`; status GET with no pending test has no `hx-trigger`; shared-system note lists the other company's name; aloha tab has no password field; a company with an empty `provider_key` shows the system select and no services form.
- [ ] **Step 2: Run** `.venv/bin/python manage.py test companies.tests_connection` → FAIL.
- [ ] **Step 3: Implement.** Follow the existing tab markup and `rd-*` classes in `company_detail.html` / `company_detail_general.html`; switches use `form-check form-switch` like the old providers general tab. Load `providers.health.status()` only when the connection tab is active.
- [ ] **Step 4: Run** `companies` tests + full suite → PASS.
- [ ] **Step 5: Commit:** `git commit -m "feat(companies): provider connection tab on the company page"`.

### Task 6: Phone-refresh providers page keeps refresh-only config

**Files:**
- Modify: `phone_refresh/views/settings.py` (drop `provider_account_save`, `providers_general_save`, `_provider_configs_ordered`, `PROVIDERS_WITH_ACCOUNTS`, health/account context), `phone_refresh/urls.py` (drop the two routes), `phone_refresh/views/__init__.py`, `phone_refresh/templates/phone_refresh/_providers_general_tab.html` (keep number test; replace toggles/health with a note linking each company's connection tab), `phone_refresh/admin.py` and `phone_refresh/forms.py` (remove `ProviderConfig` admin + form), page title "مزوّدو تحديث الأرقام" → "قواعد مزوّدي التحديث"
- Delete: `phone_refresh/templates/phone_refresh/_provider_account_card.html`
- Test: `phone_refresh/tests_internal_test_screens.py`, `providers/tests/test_accounts.py`, `providers/tests/test_service.py` (move/delete tests of the removed views; account-form behaviour is covered by Task 5)

**Interfaces:**
- Consumes: Task 5 URL `companies:company_detail` with `?tab=connection`.

- [ ] **Step 1: Failing tests:** providers page General tab contains a link to `company_detail` `?tab=connection` for each company with a `provider_key`, and contains no `is_enabled_` inputs and no password input; `reverse("phone_refresh:provider_account_save", ...)` raises `NoReverseMatch`; `?tab=sky` still renders the rules tab.
- [ ] **Step 2: Run** the phone_refresh tests → FAIL.
- [ ] **Step 3: Implement.** Afterwards `rg -n "ProviderConfig" --glob '!**/migrations/**' --glob '!docs/**' .` matches only `phone_refresh/models.py` (model kept for next release) and the Task 1 migration test.
- [ ] **Step 4: Run** full suite → PASS.
- [ ] **Step 5: Commit:** `git commit -m "refactor(phone_refresh): providers page keeps refresh rules only"`.

### Task 7: Dashboard "حالة الربط" alert

**Files:**
- Modify: `providers/health.py` (add `alerts`), `reports/views.py:23,344`, `templates/reports/dashboard.html` (replace the queue-stalled block)
- Test: `reports/tests.py`, `providers/tests/test_health_alerts.py`

**Interfaces:**
- Produces: `health.alerts() -> list[dict]` with items `{"text": str, "url": str}`; cached 30 s in Django cache key `providers:health-alerts`; entries: Redis down ("Redis لا يستجيب"), worker down or `refresh_queue_stalled()` ("الـ worker متوقف أو الطلبات عالقة"), and per enabled connection with `last_login_ok is False` ("فشل تسجيل الدخول: <company name>" → `company_detail?tab=connection` of the first company with that `provider_key`). Empty list when healthy.

- [ ] **Step 1: Failing tests:** healthy (mock `_redis_ok`/`_worker_ok` True, no failed logins) → `[]` and dashboard has no "حالة الربط"; Redis down → one item; enabled sky connection with `last_login_ok=False` and a company with `provider_key="sky"` → item whose url ends `?tab=connection`; disabled connection with failed login → no item; dashboard renders the alert block with each text when `alerts()` is non-empty.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**; `health.status()` keeps working for the connection tab.
- [ ] **Step 4: Run** full suite → PASS.
- [ ] **Step 5: Commit:** `git commit -m "feat(reports): provider connection health alert on the dashboard"`.

### Task 8: Docs, verification, deploy

**Files:**
- Modify: `DEPLOY_COOLIFY.md` (Sky account and switches now live in **Companies → <company> → الربط مع المزوّد**; rollout step 2 and troubleshooting rows updated), `README.md` (providers section)

- [ ] **Step 1:** Update the docs; `rg -n "Phone Refresh → Providers" DEPLOY_COOLIFY.md README.md` returns nothing.
- [ ] **Step 2:** Full verification: `.venv/bin/python manage.py test` → OK; `node --test tests/js/` → pass; `makemigrations --check --dry-run` → no changes; `check --deploy` with production settings env → no new errors.
- [ ] **Step 3:** Commit docs: `git commit -m "docs: provider connection lives on the company page"`.
- [ ] **Step 4:** Back up production DB (`pg_dump -Fc` from container `j8dwrqecpeoyqu3z19tglff6` to `~/backups/recharge_desk/pre_connection_<ts>.dump`, copy to `~/Desktop/prosim/backups/`).
- [ ] **Step 5:** `git push origin main`; queue Coolify deploys for web `lbtrwffbeyl7mu4e7z3etdgo` **then** worker `hcrmdvh4fdldsgm7l8kdioak` (worker must have the new `test_connection` task); confirm web rolled via the health check, migrations `providers 0004` and `companies 0014` applied, worker logs list `providers.tasks.test_connection`.
- [ ] **Step 6:** Smoke on production: Sky company → connection tab renders; "اختبار تسجيل الدخول" shows success; dashboard shows no alert; public refresh page 200.
