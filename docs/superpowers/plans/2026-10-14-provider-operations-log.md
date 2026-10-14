# Provider Operations Log Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record every provider business operation (refresh, charge, charge follow-up, reconcile) as one row with a named Arabic error, and show them per provider on a new "تقارير المزودين" report page.

**Architecture:** New `providers.ProviderOperation` table and a pure classifier + never-raising recorder in `providers/operations.py`. Charge, follow-up and reconcile record inside `providers.service`; refresh records in `phone_refresh` `complete()` / `complete_failure()` where the provider reply becomes a final status. A management report view reads only the new table.

**Tech Stack:** Django 4.2, Python 3.9 (`.venv`, `from __future__ import annotations`), Celery, HTMX/Bootstrap `rd-*`.

**Spec:** `docs/superpowers/specs/2026-10-14-provider-operations-log-design.md`

## Global Constraints

- Message format exactly `فشل <الخدمة> — <السبب>` (em dash with spaces); service labels: `refresh` → "التحديث", `number_info` → "بيانات الرقم", `charge` → "الشحن", `check_charge` → "متابعة الشحن", `reconcile` → "المطابقة".
- Cause texts exactly as the spec table: "خطأ تسجيل الدخول", "تسجيل الدخول قيد التنفيذ، حاول بعد قليل", "الخدمة موقوفة", "بيانات الحساب ناقصة", "تعذّر الاتصال بالمزوّد", "رفض المزوّد: <reason>", "خطأ في عملية <الخدمة>", "خطأ داخلي"; pending charge "الشحن قيد المتابعة"; success "تم <الخدمة>" (+ " — رقم العملية <ref>" when there is a ref).
- `outcome` ∈ `success` / `failed` / `pending`; `source` ∈ `web` / `sms` / `api` / `employee` / `management` / `system`.
- `detail` always `scrub()`-ed and ≤ 500 chars; `message` ≤ 255.
- Recording never raises and never breaks the caller's transaction (savepoint).
- Customer-facing refresh messages and `RefreshLog` unchanged.
- Retention env `PROVIDER_OPERATIONS_RETENTION_DAYS`, default 90.
- Report page and all new views `@management_required`; UI copy Arabic; page title/nav "تقارير المزودين".
- Tests: `.venv/bin/python manage.py test <label>`; full suite + `makemigrations --check --dry-run` green. Stage explicit paths only; never push.

## Review Focus

- `record()` called inside `apply_outcome`'s or a caller's `transaction.atomic()` when the insert fails → the outer transaction must still commit (use `transaction.atomic()` savepoint inside `record`). Test in Task 1.
- A refresh that needed an automatic login and then succeeded → exactly one `success` row, no login row. Test in Task 3.
- Provider reply containing a password/token/cookie → `detail` scrubbed. Test in Task 1.
- Charge outcome message codes like `not_sent:login_failed` → employee sees "فشل الشحن — خطأ تسجيل الدخول" in `Sale.charge_message`. Test in Task 2.
- Report filters with an unknown provider tab / bad dates / phone with spaces → page renders with defaults, no 500. Test in Task 4.

---

### Task 1: `ProviderOperation` model, classifier, recorder, retention

**Files:**
- Modify: `providers/models.py` (add model), `providers/tasks.py` (`purge_old_jobs` also purges operations), `config/settings/base.py` (`PROVIDER_OPERATIONS_RETENTION_DAYS = int(os.environ.get(..., "90"))`)
- Create: `providers/migrations/0005_provider_operation.py`, `providers/operations.py`
- Test: `providers/tests/test_operations.py`

**Interfaces:**
- Produces (`providers/operations.py`):
  - constants `SERVICE_LABELS: dict[str, str]`, `OUTCOME_SUCCESS/FAILED/PENDING`, `SOURCES` (tuple of the six source codes)
  - `@dataclass(frozen=True) class Classified: outcome: str; error_code: str; message: str`
  - `success(service: str, ref: str = "") -> Classified`
  - `classify_code(service: str, code: str) -> Classified` — maps a provider/gateway error code (optionally `not_sent:<code>`) to a failed `Classified`
  - `rejected(service: str, reason: str) -> Classified`
  - `classify_exception(service: str, exc: BaseException) -> Classified`
  - `classify_charge(service: str, outcome: ChargeOutcome) -> Classified` (`service` is `charge` or `check_charge`)
  - `record(provider_key: str, service: str, classified: Classified, *, phone: str = "", source: str = "system", user_id: int | None = None, sale_id: int | None = None, job_id=None, attempts: int = 1, duration_ms: int | None = None, detail: str = "") -> None`
- Produces (`providers/models.py`): `ProviderOperation` with the spec's fields (`user` = FK `settings.AUTH_USER_MODEL`, null, `SET_NULL`, `related_name="+"`); `Meta.indexes` on (`provider_key`, `-created_at`); `ordering = ["-created_at"]`.

Code → cause table for `classify_code` (after stripping a `not_sent:` prefix):

| codes | error_code |
|---|---|
| `login_failed`, `login_error`, `otp_required`, `auth_required` | `login_failed` |
| `login_busy` | `login_busy` |
| `provider_disabled` | `service_off` |
| `config_error`, `not_configured` | `not_configured` |
| `connection_error`, `proxy_error`, `session_error`, `sync_budget_exceeded`, `timeout` | `connection` |
| `not_found` / `not_active` / `not_eligible` | `rejected` with "الرقم غير موجود" / "الرقم غير مفعّل" / "الرقم غير مؤهّل" |
| `context_error`, `unknown_provider` | `internal` |
| anything else (incl. `api_error`, free text) | `provider_error` |

`classify_exception`: `TransientError`/`PermanentError`/`AuthRequired` → `classify_code(str(exc))` (`AuthRequired` → `login_failed`); any other exception → `internal`.
`classify_charge`: `SUCCESS` → `success(service, outcome.provider_ref)`; `UNVERIFIED` → `Classified("pending", "", "الشحن قيد المتابعة")`; `FAILED` → `classify_code(service, outcome.message)`.

- [ ] **Step 1: Failing tests** in `providers/tests/test_operations.py`:
  - one test per row of the code table, asserting `error_code` and exact `message`, e.g. `classify_code("charge", "not_sent:login_failed") == Classified("failed", "login_failed", "فشل الشحن — خطأ تسجيل الدخول")`; `classify_code("refresh", "something weird").message == "فشل التحديث — خطأ في عملية التحديث"`; `classify_code("refresh", "not_found").message == "فشل التحديث — رفض المزوّد: الرقم غير موجود"`
  - `success("charge", "123").message == "تم الشحن — رقم العملية 123"`; `success("reconcile").message == "تم المطابقة"`
  - `classify_charge` for SUCCESS / UNVERIFIED / FAILED(`not_sent:login_busy`)
  - `classify_exception(ValueError("x"))` → `internal`, message "فشل … — خطأ داخلي"
  - `record` writes one row with all passed fields; `detail` containing `password=hunter2` is stored scrubbed and ≤ 500 chars; `message` truncated to 255
  - `record` with `ProviderOperation.objects.create` patched to raise `DatabaseError` → no exception, warning logged; called inside `transaction.atomic()` followed by another write → the outer write is committed (use `TransactionTestCase` or `captureOnCommitCallbacks`-free check: assert the other row exists after the block)
  - `purge_old_jobs()` deletes operations older than `PROVIDER_OPERATIONS_RETENTION_DAYS` (override_settings 90) and keeps a newer one
- [ ] **Step 2: Run** `.venv/bin/python manage.py test providers.tests.test_operations` → FAIL (import errors).
- [ ] **Step 3: Implement** model + migration (`makemigrations providers -n provider_operation`), `operations.py`, setting, purge (read `settings.PROVIDER_OPERATIONS_RETENTION_DAYS`). `record` wraps the create in `transaction.atomic()` and catches `Exception`.
- [ ] **Step 4: Run** focused tests → PASS; then `.venv/bin/python manage.py test providers` → PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(providers): provider operations log model, classifier and recorder"`.

### Task 2: Record charge, follow-up and reconcile; named charge message

**Files:**
- Modify: `providers/service.py` (`charge`, `check_charge`, `reconcile`), `sales/tasks.py` (`_charge`, `_settle`), `companies/views.py` (`layan_reconcile`, `sky_reconcile`)
- Test: `providers/tests/test_service_operations.py`; update existing assertions on raw charge messages in `sales/tests_charging.py` / `providers/tests/test_service.py`

**Interfaces:**
- Consumes: Task 1 `classify_charge`, `classify_exception`, `success`, `record`.
- Produces:
  - `charge(provider_key, ctx, budget=SYNC_BUDGET_SECONDS, *, source: str = "employee", user_id: int | None = None, job_id=None) -> ChargeOutcome`
  - `check_charge(provider_key, ctx, provider_ref, budget=SYNC_BUDGET_SECONDS, *, source: str = "system", job_id=None) -> ChargeOutcome`
  - `reconcile(company, period_from, period_to, *, budget=SYNC_BUDGET_SECONDS, source: str = "management", user_id: int | None = None, **opts)`
- Behaviour: `charge`/`check_charge` record one row (phone `ctx.phone`, `sale_id=ctx.sale_id`, duration, `detail` = the original outcome message / raw excerpt) and return the outcome with `message` replaced by the classified Arabic message (result, ref, plan, cost unchanged). `reconcile` records success (`success("reconcile")`, detail `"<row_count> rows"` when the result has `row_count`) or, on any exception including the `RECONCILE_NOT_SUPPORTED` gate, a failed row via `classify_exception` (gate → `service_off` cause using the `service_state` code: `off` → `service_off`, `not_configured` → `not_configured`), then re-raises unchanged. Phone blank for reconcile.
- Callers: `sales/tasks._charge` passes `source="employee", user_id=sale.created_by_id, job_id=job.pk`; `_settle` passes `job_id=job.pk`; reconcile views pass `user_id=request.user.pk`.

- [ ] **Step 1: Failing tests** in `providers/tests/test_service_operations.py` (fake adapter registered via `ADAPTERS` patch, as existing service tests do):
  - charge SUCCESS → one row `outcome="success"`, `sale_id`, `user_id`, `job_id`, `source="employee"`, message "تم الشحن — رقم العملية <ref>"
  - charge where the login fails (`run_authenticated` raises `TransientError("login_failed")` before sending) → returned `ChargeOutcome.result == FAILED`, `message == "فشل الشحن — خطأ تسجيل الدخول"`, one row `error_code="login_failed"`
  - charge UNVERIFIED → row `outcome="pending"`
  - check_charge FAILED → row `service="check_charge"`, `source="system"`
  - reconcile success → one row `service="reconcile"`, `source="management"`, `user_id`; reconcile raising `TransientError("connection_error")` → re-raised, row `error_code="connection"`; gate (connection switched off) → `PermanentError(RECONCILE_NOT_SUPPORTED)` re-raised, row `error_code="service_off"`
  - `record` patched to raise → charge still returns its outcome
- [ ] **Step 2: Run** `.venv/bin/python manage.py test providers.tests.test_service_operations` → FAIL.
- [ ] **Step 3: Implement** in `providers/service.py`; wire callers; update existing tests that asserted raw codes in `Sale.charge_message` to the Arabic messages (add one to `sales/tests_charging.py`: a sale whose charge fails with `not_sent:login_failed` ends with `charge_message == "فشل الشحن — خطأ تسجيل الدخول"`).
- [ ] **Step 4: Run** `.venv/bin/python manage.py test providers sales companies` → PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(providers): record charge, follow-up and reconcile operations"`.

### Task 3: Record refresh operations

**Files:**
- Modify: `providers/contracts.py` (`RawResponse.attempts: int = 1`), `providers/service.py` (`refresh` sets `attempts` on every returned `RawResponse`), `phone_refresh/services/refresh_service.py` (`complete`, `complete_failure` gain `job_id=None` and record), `phone_refresh/tasks.py` (`_execute` passes `job_id=job.pk`)
- Test: `phone_refresh/tests_operations_log.py`

**Interfaces:**
- Consumes: Task 1 `success`, `classify_code`, `rejected`, `record`.
- Source mapping from `RefreshSource`: `web`→`web`, `sms`→`sms`, `api`→`api`, `employee`→`employee`, `internal_test`→`management`; anything else → `system`.
- Outcome mapping in `complete()`: `raw.error` → `classify_code("refresh", raw.error)`; matched status `refreshed` → `success("refresh", <task id from raw.json["task_id"] if present>)`; any other status → `rejected("refresh", status.label)`. `complete_failure()` with the default `status_code="error"` (provider crashed) → `classify_code("refresh", "context_error")` semantics, i.e. `error_code="internal"`, message "فشل التحديث — خطأ داخلي"; with `status_code="busy"` (job could not be enqueued) → `classify_code("refresh", "login_busy")`. `detail` = the same scrubbed excerpt `RefreshLog.raw_excerpt` gets; `duration_ms` = the RefreshLog duration; `attempts` = `raw.attempts`.
- Pre-checks that return before calling the provider write no row.

- [ ] **Step 1: Failing tests** in `phone_refresh/tests_operations_log.py` (provider refresh patched as in existing refresh tests):
  - sync web refresh that matches `refreshed` → one row `service="refresh"`, `source="web"`, `outcome="success"`
  - refresh where `provider_service.refresh` returns `RawResponse(error="login_failed")` → row message "فشل التحديث — خطأ تسجيل الدخول"; customer payload unchanged from today (assert `result.status.code == "busy"`)
  - refresh whose reply matches a `not_found` rule → row `error_code="rejected"`, message contains the status label
  - adapter raising `AuthRequired` once then succeeding through `run_authenticated` (patch adapter + login) → exactly one row, `success`
  - provider crash (`complete_failure`) → row `error_code="internal"`
  - async job (`run_refresh_job`) → row has `job_id == job.pk` and `source` from the job
  - rate-limited / invalid-number pre-check → zero rows
  - internal test source → `source="management"`
  - `providers.service.refresh` returning after 2 attempts → row `attempts == 2`
- [ ] **Step 2: Run** `.venv/bin/python manage.py test phone_refresh.tests_operations_log` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `.venv/bin/python manage.py test phone_refresh providers` → PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(phone_refresh): record refresh operations in the provider log"`.

### Task 4: "تقارير المزودين" report page

**Files:**
- Create: `reports/views_provider_ops.py`, `templates/reports/provider_operations.html`
- Modify: `reports/urls.py` (`path("management/reports/providers/", views_provider_ops.provider_operations, name="provider_operations")`), `templates/partials/management_nav.html` (link "تقارير المزودين" in the reports group, active when `un == 'provider_operations'`)
- Test: `reports/tests_provider_ops.py`

**Interfaces:**
- Consumes: Task 1 `ProviderOperation`, `SERVICE_LABELS`, `SOURCES`.
- View `provider_operations(request)`: GET params `provider` (registry key; invalid/missing → first key with rows in range, else `sky`), `period` (`today` / `7d` default / `30d` / `custom` with `from`, `to` as `YYYY-MM-DD`; invalid dates → `7d`), `service`, `outcome`, `source` (ignored when not a known value), `phone` (stripped, `icontains`). Context: `providers` (tabs with counts), `summary` = `{"total","success","failed","pending"}`, `per_service` = list of `(label, count, success_rate_pct)`, `top_causes` = up to 5 `(cause message without the "فشل <الخدمة> — " prefix grouped by error_code, count)`, `page_obj` via `core.pagination.paginate_request` (50 per page).
- Template: provider tabs, filter form (GET), summary cards, per-service lines, top causes, table (time, service label, phone, outcome badge `rd-badge--paid` / `rd-badge--cancelled` / `rd-badge--pending`, message, source label + user name, duration in seconds with one decimal, sale link `sales:sale_edit` when `sale_id`), `<details>` with `detail` (auto-escaped). Follow `templates/reports/sales_report.html` structure and `rd-*` classes.

- [ ] **Step 1: Failing tests** in `reports/tests_provider_ops.py`:
  - employee → redirected/403; management → 200 with "تقارير المزودين"
  - rows for sky and layan → `?provider=sky` shows only sky rows; tab list shows both
  - each filter (`service`, `outcome`, `source`, `phone=" 0591 "` stripped, `period=today` excludes a 2-day-old row, `custom` range)
  - `?provider=nope&period=custom&from=bad` → 200 with defaults
  - summary counts and success rate; top causes ordered by count ("خطأ تسجيل الدخول" ×3 before "تعذّر الاتصال بالمزوّد" ×1)
  - pagination: 60 rows → page 2 has 10
  - `detail="<script>x</script>"` rendered escaped
  - sale link present for a row with `sale_id`
  - nav contains a link to `reports:provider_operations`
- [ ] **Step 2: Run** `.venv/bin/python manage.py test reports.tests_provider_ops` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `.venv/bin/python manage.py test reports` → PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(reports): provider operations report page"`.

### Task 5: Docs and full verification

**Files:**
- Modify: `README.md` (providers section: operations log + report page), `DEPLOY_COOLIFY.md` (optional env `PROVIDER_OPERATIONS_RETENTION_DAYS`; operator note: where to see provider operations), `.env.example` (commented `PROVIDER_OPERATIONS_RETENTION_DAYS=90`)

- [ ] **Step 1:** Update docs.
- [ ] **Step 2:** Run `.venv/bin/python manage.py test` → OK; `node --test tests/js/*.test.js` → pass; `.venv/bin/python manage.py makemigrations --check --dry-run` → "No changes detected".
- [ ] **Step 3: Commit** `git commit -m "docs: provider operations log and report"`.
- [ ] **Step 4 (controller, after the user's go-ahead):** back up the production DB, merge, push, deploy web then worker, smoke the report page.
