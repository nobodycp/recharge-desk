# Provider operations log + "تقارير المزودين" — design

Date: 2026-10-14. Phase 1 of the Sky work (phase 2 = Sky number info, phase 3 = Sky charge with
price check and follow-up; each gets its own spec).

## Goal

Every business operation the system runs against a provider (Sky, Aloha, Layan, Areen) is
recorded in one place, with an error that names the service and the cause
("فشل الشحن — خطأ تسجيل الدخول"). Management sees each provider's operations separately on a new
report page, "تقارير المزودين".

## Decisions (agreed with the user)

- Sky keeps talking to the Sky Sales portal API (`sales-ps.sky5g.ps`); that is the "official" API
  for us. No change of transport in this phase.
- Work is split into three phases; this spec is phase 1 only.
- The report lives under Reports, named "تقارير المزودين".
- Only business operations are recorded: refresh, number info (phase 2), charge, charge follow-up,
  reconcile. Logins and keep-alive pings get no rows of their own; a failed login shows up as the
  cause of the operation that needed it.
- Approach A: one new table for all provider operations, written from `providers.service`.

## Non-goals

- Sky number info, Sky charge, price matching (phases 2 and 3).
- Excel export, new alerts. The dashboard "حالة الربط" block stays as is.
- Changing customer-facing refresh messages (public page, SMS, API): they stay rule-driven
  (`ProviderResponseRule` / `CustomerMessage`). Customers never see the named staff errors.
- Replacing `RefreshLog` or its reports; it stays the refresh audit log.

## Data: `providers.ProviderOperation`

One row per business operation (retries and an automatic login inside the operation do not add
rows).

| Field | Type | Notes |
|---|---|---|
| `provider_key` | char(32), indexed | registry key |
| `service` | char(20) | `refresh`, `number_info`, `charge`, `check_charge`, `reconcile` |
| `phone` | char(20), indexed, blank | blank for reconcile |
| `outcome` | char(12) | `success`, `failed`, `pending` (charge sent but unconfirmed) |
| `error_code` | char(24), blank | cause code (table below); blank on success |
| `message` | char(255) | Arabic staff message, e.g. "فشل الشحن — خطأ تسجيل الدخول" |
| `source` | char(20) | `web` (public page), `sms`, `api`, `employee`, `management`, `system` (tasks) |
| `user` | FK user, null, `SET_NULL` | the employee/manager when known |
| `sale_id` | int, null | no FK (providers never imports sales) |
| `job_id` | UUID, null | `ProviderJob` when the operation ran as a job |
| `attempts` | small int, default 1 | refresh retries |
| `duration_ms` | int, null | wall time of the operation |
| `detail` | text, blank | scrubbed provider response excerpt, max 500 chars |
| `created_at` | datetime, indexed | |

Index on (`provider_key`, `created_at`). Retention: `PROVIDER_OPERATIONS_RETENTION_DAYS`
(env, default 90), purged by the existing daily `providers.tasks.purge_old_jobs` beat task.

## Error causes and messages

Message format: `فشل <الخدمة> — <السبب>`. Service names: تحديث الأرقام → "التحديث",
"بيانات الرقم", "الشحن", "متابعة الشحن", "المطابقة".

| `error_code` | Raised when | Cause text |
|---|---|---|
| `login_failed` | `TransientError("login_failed")` (bad credentials, OTP, cool-down) | خطأ تسجيل الدخول |
| `login_busy` | `TransientError("login_busy")` | تسجيل الدخول قيد التنفيذ، حاول بعد قليل |
| `service_off` | `PermanentError("provider_disabled")` or switch off | الخدمة موقوفة |
| `not_configured` | `PermanentError("config_error")` / missing credentials | بيانات الحساب ناقصة |
| `connection` | `TransientError` connection/proxy/timeout, `sync_budget_exceeded` | تعذّر الاتصال بالمزوّد |
| `rejected` | provider answered "no" (Sky `not_found`, `not_active`, `not_eligible`, ...) | رفض المزوّد: <reason in Arabic> |
| `provider_error` | other provider-side failure during the operation | خطأ في عملية <الخدمة> |
| `price_mismatch` | reserved for phase 3 | السعر غير مطابق (النظام <x>، المزوّد <y>) |
| `internal` | unexpected exception in our code | خطأ داخلي |

`pending` outcome (charge `UNVERIFIED`) is not a failure: message "الشحن قيد المتابعة".
Success messages: "تم <الخدمة>" plus the provider reference when there is one.

Known Sky reasons map to Arabic reasons (`not_found` → "الرقم غير موجود", `not_active` →
"الرقم غير مفعّل", `not_eligible` → "الرقم غير مؤهّل"); an unknown reason falls back to
`provider_error` with the excerpt kept in `detail`.

## Components

- `providers/operations.py`
  - `classify(...)` — turns a `RawResponse`, `ChargeOutcome`, or exception into
    `(outcome, error_code, message)`; pure function, no DB.
  - `record(provider_key, service, *, phone="", source, user=None, sale_id=None, job_id=None,
    attempts=1, duration_ms=None, outcome, error_code="", message, detail="")` — writes one row;
    any exception is logged as a warning and swallowed (recording never breaks an operation).
- `providers/service.py` — `refresh`, `charge`, `check_charge`, `reconcile` gain keyword-only
  `source`, `user=None`, `sale_id=None`, `job_id=None` (defaults keep current callers working) and
  record exactly one row each before returning or re-raising. Return values and exceptions are
  unchanged.
- Callers pass their context: `phone_refresh/services/refresh_service.py` (sync: web/sms/api/
  employee), `phone_refresh/tasks.py` (async refresh jobs, source from the job), `sales/tasks.py`
  (charge: `employee` + user + sale; follow-up: `system` + sale), `companies/views.py`
  (reconcile: `management` + user).
- Employee charge message: `sales/charging.py` stores/shows the classified Arabic message instead
  of raw codes like `not_sent:login_failed`.
- Report page: `reports/views_provider_ops.py` + `templates/reports/provider_operations.html`,
  URL `reports:provider_operations` → `management/reports/providers/`, `@management_required`,
  nav link "تقارير المزودين" in the Reports group of `templates/partials/management_nav.html`.

## Report page "تقارير المزودين"

Top to bottom:

1. Provider tabs (one per registry key; default the first with rows, else `sky`).
2. Filters (GET params): period `today` / `7d` (default) / `30d` / custom from–to; `service`;
   `outcome`; `phone` (contains); `source`.
3. Summary for the filtered set: cards (total, success, failed, pending); one line per service
   (count, success rate); top failure causes by `error_code` with counts.
4. Table, newest first, paginated like the other reports (50 per page): time, service, phone,
   outcome badge (green/red/amber), message, source (+ user name), duration, sale link
   (`sales:sale_edit` when `sale_id`). A row expands (HTML `<details>`) to show `detail`.

Follows the existing report markup and `rd-*` classes; Arabic copy.

## Error handling

- `record` never raises; a DB error is logged and the operation result is returned unchanged.
- `detail` is always passed through `providers.sanitize.scrub` and truncated to 500 chars.
- Classification never inspects secrets; unknown shapes fall back to `provider_error` /
  `internal`.

## Deployment

One new table (additive migration); the old container ignores it during the rolling deploy.
New env var `PROVIDER_OPERATIONS_RETENTION_DAYS` is optional. Web then worker, as usual.

## Testing

- `classify`: every row of the cause table, Sky reasons, unknown reason, pending charge, success.
- Each service function writes exactly one row (including a refresh that retried twice and a
  refresh that needed a login), with the passed context (`source`, `user`, `sale_id`, `job_id`).
- A failing `record` (DB error patched) does not change the operation's result.
- Charge failure shows the named Arabic message to the employee.
- Report page: management-only; provider tabs; each filter; summary counts and top causes;
  pagination; `detail` rendered escaped.
- Purge removes rows older than the retention and keeps newer ones.
