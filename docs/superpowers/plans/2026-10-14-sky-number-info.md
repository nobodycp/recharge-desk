# Sky Number Info ("فحص الرقم") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A «فحص الرقم» button on the employee page (renamed «فحص الأرقام») that reads the line's live data from Sky through the worker and shows it with the sale price of the number's last sale; one operations row per check.

**Architecture:** New `NUMBER_INFO` capability and `NumberInfo` result in the provider contract; Sky parses the subscriber-search row it already fetches. `providers.service.number_info` runs it authenticated and records the row; a `number_info` `ProviderJob` runs it on the worker. `sales/number_check.py` routes by the last sale, gates, submits/joins the job and renders the card; the page polls like refresh.

**Tech Stack:** Django 4.2, Python 3.9 (`.venv`, `from __future__ import annotations`), Celery (eager + fakeredis in tests), Bootstrap `rd-*`, plain JS + node tests.

**Spec:** `docs/superpowers/specs/2026-10-14-sky-number-info-design.md`

## Global Constraints

- `providers` never imports `sales`; `sales` may import `providers`.
- Messages exactly as the spec table; service label `number_info` → «فحص الرقم», success «تمّ فحص الرقم», new cause `not_found` → «الرقم غير موجود لدى المزوّد». Phase-1 refresh messages unchanged (the refresh `not_found` reject reason stays as it is).
- Sky price (`rate_plan_price`) is stored on the job result but never sent to the browser (no raw `NumberInfo` dict in any JSON response).
- Exactly one `ProviderOperation` per check that reaches the gate; none for invalid number / no sale / unsupported / enqueue failure.
- Never a provider login in a web request: the provider call runs only in the worker task (eager in tests).
- UI copy Arabic (hard-coded like phase 1), Western digits; card HTML auto-escaped, no `|safe` on provider data; JS inserts only server-rendered HTML.
- «تحديث الرقم» behaviour, its endpoints and `employee-refresh-number.js` behaviour unchanged.
- Tests: `.venv/bin/python manage.py test <label>`; full suite + `makemigrations --check --dry-run` + `node --test tests/js/*.test.js` green. Stage explicit paths only; never push.

## Review Focus

- `search_subscriber` returns `rows[0]` on no exact match → number info must report not found, not another line's data. Test in Task 1.
- Login-then-success → exactly one success row, attempts 1. Test in Task 2.
- A second press while a check is running joins the same job (no second Sky call). Test in Task 3.
- `rate_plan_price` absent from every JSON response. Test in Task 3.
- Garbage Sky values (`"null"`, `""`, `"abc"`) → «—», never an exception. Test in Task 1.

---

### Task 1: Contract, Sky parser, adapter, message labels

**Files:**
- Modify: `providers/contracts.py`, `providers/adapters/sky/adapter.py`, `providers/operations.py`
- Create: `providers/adapters/sky/number_info.py`
- Test: `providers/tests/test_sky_number_info.py`; update `providers/tests/test_operations.py` for the label change

**Interfaces:**
- `Capability.NUMBER_INFO = "number_info"`.
- `@dataclass class NumberInfo` (all optional except strings default `""`): `network: str`, `is_esim: bool`, `status: str`, `plan_name: str`, `plan_price: Decimal | None`, `ends_at: datetime | None` (aware, project tz), `data_used_gb: Decimal | None`, `data_total_gb: Decimal | None`, `minutes_used: int | None`, `minutes_total: int | None`, `sms_used: int | None`, `sms_total: int | None`, `utilization_pct: Decimal | None`, `iccid: str`. `to_dict() -> dict` (JSON-safe: Decimal → str, datetime → ISO) and `@classmethod from_dict(d) -> NumberInfo` (round-trips exactly; tolerates missing keys).
- `BaseAdapter.number_info(self, phone: str) -> NumberInfo` → `NotImplementedError`. Raise `PermanentError("not_found")` when the provider has no such line.
- `providers/adapters/sky/number_info.py`: `matches_phone(row: dict, phone: str) -> bool` (`ne_id == phone` or `msisdn` endswith `phone.lstrip("0")`); `parse_number_info(row: dict) -> NumberInfo`:
  - `ends_at` from `rate_plan_end_date_time` (`%d/%m/%Y %H:%M`, made aware in `Asia/Jerusalem` via `timezone.get_default_timezone()`); fallback `rate_plan_end_date` (`%d/%m/%Y`, 00:00) only when the datetime is missing/bad.
  - `data_total_gb = allocated_mb / 1024`; `data_used_gb = total_data_in_billing_cycle`; minutes/sms from `total_voice/sms_in_billing_cycle` and `allocated_minutes/sms`; `utilization_pct = utilization`; `plan_price = rate_plan_price`.
  - Each field parsed independently; `None`/`"null"`/`""`/unparseable → `None` (or `""` for strings).
- `SkyAdapter`: add `Capability.NUMBER_INFO`; `number_info(phone)`: `_stored_client()`, `with _sky_errors(): row = client.search_subscriber(phone)`; no row or `not matches_phone` → `PermanentError("not_found")`; else `parse_number_info(row)`.
- `operations.py`: `SERVICE_LABELS["number_info"] = "فحص الرقم"`, `SUCCESS_LABELS["number_info"] = "تمّ فحص الرقم"`, `CAUSE_LABELS["not_found"] = "الرقم غير موجود لدى المزوّد"`, and `not_found(service) -> Classified` (`failed`, `not_found`, `فشل <label> — الرقم غير موجود لدى المزوّد`).

- [ ] **Step 1: Failing tests** — fixtures = the two spec rows with name/ICCID anonymised:
  - eSIM row → `is_esim True`, network «سلكوم», plan «حملة رمضان 400 جيجا سلكوم», `ends_at` local == 2026-11-13 23:30, `data_used_gb == Decimal("6.08")`, `data_total_gb == Decimal("400")`, minutes 0/5000, sms 0/5000, `utilization_pct == Decimal("1.52")`, `plan_price == Decimal("29")`.
  - physical row (`esim ""`) → `is_esim False`; `"ESIM"` → True.
  - garbage values per field → `None`, no exception; missing datetime → falls back to end date.
  - `to_dict`/`from_dict` round-trip equal; `json.dumps(to_dict())` works.
  - adapter: client returns a non-matching row → `PermanentError("not_found")`; `None` → same; matching → parsed; `SkySalesSessionError` → `AuthRequired`; `requests.ConnectionError` → `TransientError("connection_error")` (patch `SkySalesClient.from_store` / `search_subscriber`, like `providers/tests/test_sky_adapter.py`).
  - `operations.not_found("number_info").message == "فشل فحص الرقم — الرقم غير موجود لدى المزوّد"`; `success("number_info").message == "تمّ فحص الرقم"`.
- [ ] **Step 2: Run** `.venv/bin/python manage.py test providers.tests.test_sky_number_info` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `.venv/bin/python manage.py test providers` → PASS.
- [ ] **Step 5: Commit** `feat(providers): number info contract and Sky parser`.

### Task 2: Service, job kind, worker task, retention

**Files:**
- Modify: `providers/service.py`, `providers/models.py`, `providers/tasks.py`
- Create: `providers/migrations/0006_providerjob_number_info.py`
- Test: `providers/tests/test_number_info_service.py`

**Interfaces:**
- `@dataclass(frozen=True) class NumberInfoResult: info: NumberInfo | None; classified: Classified` (`ok` property = `info is not None`).
- `number_info(provider_key: str, phone: str, budget: float = SYNC_BUDGET_SECONDS, *, source: str = "employee", user_id: int | None = None, sale_id: int | None = None, job_id=None) -> NumberInfoResult`: same attempt/retry/deadline loop as `refresh` (transient retried with `REFRESH_RETRY_DELAYS`, `NO_RETRY_TRANSIENT_CODES` not retried) around `run_authenticated(adapter, lambda: adapter.number_info(phone), lock_wait=...)`. `PermanentError("not_found")` → `operations.not_found("number_info")`; other errors → `classify_exception("number_info", exc)`; budget exhausted → `classify_code("number_info", "sync_budget_exceeded")`; success → `success("number_info")`. Records exactly one row (`attempts` = attempts started, `duration_ms`, `detail` = scrubbed error text on failure). Never raises except `KeyError` for an unknown key.
- `ProviderJob.KIND_NUMBER_INFO = "number_info"` added to `KIND_CHOICES` (+ migration `AlterField` choices only).
- `providers/tasks.py`: `NUMBER_INFO_BUDGET_SECONDS = 60`; `@shared_task(time_limit=jobs.JOB_TASK_TIME_LIMIT, soft_time_limit=jobs.JOB_TASK_SOFT_TIME_LIMIT) run_number_info_job(job_id: str) -> None` — load (bad id/missing/wrong kind → log + return), `jobs.mark_running` (False → return), call `service.number_info(job.provider_key, job.phone, NUMBER_INFO_BUDGET_SECONDS, source=job.source, user_id=payload.get("user_id"), sale_id=payload.get("sale_id"), job_id=job.pk)`, close stale DB connection when not eager (like `phone_refresh.tasks._refresh_db_connection`), then `mark_done(job, {"status": "ok", "info": info.to_dict()})` or `mark_failed(job, {"status": "error", "message": classified.message, "error_code": classified.error_code})`. Any crash → `mark_failed` with `operations.classify_exception("number_info", exc)` message (no second row if the service already recorded). Never raises.
- `purge_old_jobs` deletes `kind__in=[KIND_REFRESH, KIND_NUMBER_INFO]` past retention.

- [ ] **Step 1: Failing tests** (fake adapter registered via `patch.dict(ADAPTERS, ...)` or patch `SkyAdapter.number_info`; fakeredis flushed in `setUp`):
  - success → `ok`, one row `success`, message «تمّ فحص الرقم», source/user/sale/job ids stored.
  - `AuthRequired` once then success (real `run_authenticated`, patched `login`) → one success row, `attempts == 1`.
  - `TransientError("connection_error")` ×3 → one failed row `connection`, `attempts == 3`; `login_busy` not retried.
  - `PermanentError("not_found")` → `not_found` row with the spec message.
  - unexpected `ValueError` in adapter → `internal`, no exception escapes.
  - task: done job holds `info` dict that `NumberInfo.from_dict` restores; failed job holds the message; wrong-kind / already-running job untouched; a second `run_number_info_job` on a finished job writes no second row.
  - `purge_old_jobs` deletes an old `number_info` job, keeps a recent one.
- [ ] **Step 2: Run** `.venv/bin/python manage.py test providers.tests.test_number_info_service` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `.venv/bin/python manage.py test providers` → PASS; `makemigrations --check --dry-run` clean.
- [ ] **Step 5: Commit** `feat(providers): number info service and worker job`.

### Task 3: Sales routing, gate, submit/poll, card

**Files:**
- Create: `sales/number_check.py`, `templates/sales/partials/number_check_card.html`
- Modify: `sales/views/employee.py`, `sales/views/__init__.py`, `sales/urls.py`
- Test: `sales/tests_number_check.py`

**Interfaces:**
- `sales/number_check.py`:
  - `last_sale_for_phone(phone: str) -> Sale | None` (`reference_number=phone`, newest `created_at`, `select_related("company", "product__line")`).
  - `@dataclass CheckTicket: job_id: str | None; payload: dict | None`.
  - `submit_number_check(phone: str, *, user) -> CheckTicket` — flow steps 1–4 of the spec. Error payload shape `{"status": "error", "message": "<Arabic>"}`. Gate row via `operations.record(..., source=_source(user), user_id=user.pk, sale_id=sale.pk)` with `classify_code("number_info", "provider_disabled" | "not_configured")`. Job `payload={"user_id": user.pk, "sale_id": sale.pk}`, `source="management" if is_management(user) else "employee"`, joined via `jobs.find_inflight(KIND_NUMBER_INFO, phone, SystemSettings.get().refresh_wait_seconds)`; per-phone Redis submit lock like `refresh_jobs._submit_lock` (key `number-info-submit:<phone>`, never blocks on Redis trouble); enqueue in `transaction.on_commit`, failure → `jobs.mark_failed` + busy payload, no row.
  - `check_payload(job_id: str) -> dict` — unknown/bad id or other kind → `{"status": "error", "message": "انتهت صلاحية الطلب، أعد المحاولة."}`; queued/running within `refresh_wait_seconds` → `{"status": "processing"}`; older → `{"status": "error", "message": "الطلب ما زال قيد المعالجة، حاول بعد دقيقة", "final": true}`; failed → `{"status": "error", "message": job.result["message"]}`; done → `{"status": "ok", "html": render_to_string(card, card_context(...))}`.
  - `card_context(info: NumberInfo, sale: Sale | None, today: date) -> dict` — formatted strings per the spec card table (days left from `localtime(ends_at).date() - today`; «تنتهي اليوم» / «منتهية»; numbers without trailing `.00` noise: `6.08`, `400`, `1.52%`; «—» for `None`); sale price line from `sale.product.default_sell_price`, `sale.product.display_name`, `localtime(sale.created_at)` date; no sale → «—».
- Views (`@employee_required`, gated by `AppSettings.load().sales_show_refresh_phone` → 403 JSON like `employee_refresh_phone`): `employee_check_phone` (`@require_POST`; 200 with payload, 202 `{"status": "processing", "job": id}`), `employee_check_status(job_id)` (`@never_cache @require_GET`). URLs next to the refresh ones: names `employee_check_phone`, `employee_check_status` (status path takes the UUID like `employee_refresh_status`).

- [ ] **Step 1: Failing tests** (eager Celery; patch `SkyAdapter.number_info` to return a `NumberInfo` built from the spec eSIM row; company `provider_key="sky"` with a connection + credentials as in `sales/tests_charging.py`/`providers/tests` helpers):
  - invalid phone / no sale → error message, no row, no job.
  - company provider `aloha` (no capability) → «فحص الرقم غير متاح لهذا المزوّد», no row.
  - connection disabled → «فشل فحص الرقم — الخدمة موقوفة» + one `service_off` row.
  - happy path via the views (`captureOnCommitCallbacks(execute=True)`): POST → 202 job; GET status → `ok` with HTML containing «سلكوم», «شريحة إلكترونية», «حملة رمضان 400 جيجا سلكوم», «13/11/2026», «متبقي 30 يوم» (freeze today = 2026-10-14 via `patch("sales.number_check.timezone.localdate")` or pass `today`), «6.08 من 400 جيجا», «1.52%», the ICCID, «60» and the product name and «14/10/2026»; exactly one operations row.
  - `"29"` (Sky price) and `"plan_price"` appear in no JSON response body.
  - second POST while the first job is queued → same job id; adapter called once.
  - enqueue failure (`run_number_info_job.delay` raises) → busy message, job failed, zero rows.
  - failed job → status returns its message; old queued job → final processing message; unknown UUID → expired message.
  - card: `None` fields → «—»; ends today → «تنتهي اليوم»; past → «منتهية»; provider string `<b>x</b>` escaped.
  - employee without the setting → 403; anonymous → login redirect.
- [ ] **Step 2: Run** `.venv/bin/python manage.py test sales.tests_number_check` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `.venv/bin/python manage.py test sales providers` → PASS.
- [ ] **Step 5: Commit** `feat(sales): number check routing, job and card`.

### Task 4: «فحص الأرقام» page — two buttons and polling

**Files:**
- Modify: `templates/sales/employee_refresh_number.html`, `sales/views/employee.py` (page title), `templates/partials/_employee_bottom_nav_inner.html` (label)
- Create: `static/js/employee-check-number.js`, `tests/js/employee_check_polling.test.js`
- Test: `sales/tests.py` (page render assertions), the node test

**Interfaces:**
- Page header/title «فحص الأرقام»; the existing refresh button label becomes «تحديث الرقم» (same id `rd-refresh-submit`, same behaviour); new button «فحص الرقم» (`id="rd-check-submit"`, `btn-outline-primary`, same row as refresh: two equal-width buttons); new result container `id="rd-check-result"` (hidden until used) below the refresh result. Root gets `data-check-url` and `data-check-status-url` (UUID placeholder like `data-status-url`). Remove the "Same routing and messages as the public refresh page…" hint line.
- Bottom nav item label for this page → «فحص الأرقام» (same URL name, active state unchanged).
- `employee-check-number.js`: reads the shared phone input `rd-refresh-phone-input`; client-side validation identical to refresh (same regex/messages); disables only the check button while working («جارٍ الفحص…»); POST with CSRF; 202 → poll status every 2 s until `ok`/`error`/`final` or `refresh_wait_seconds` (from `data-wait-seconds`) elapses; `ok` → `innerHTML = payload.html` (server-rendered, escaped); `error` → message via `textContent`; network error → generic error text. A new press cancels the previous poll. Wrapped in an IIFE; no globals.
- Follow the visual language of the page (`rd-card`, `rd-refresh-result` styles); card partial from Task 3 uses a compact two-column label/value list.

- [ ] **Step 1: Failing tests**: Django — page shows «فحص الأرقام», both buttons, both data URLs, the check script tag; refresh endpoints/tests untouched. Node — stubbed DOM/fetch/timers like `tests/js/employee_refresh_polling.test.js`: invalid phone → no fetch; 202 then `processing` then `ok` → container HTML set, button re-enabled; `error` → text shown; wait elapsed → stops polling; second press cancels first poll.
- [ ] **Step 2: Run** `.venv/bin/python manage.py test sales.tests` and `node tests/js/employee_check_polling.test.js` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `.venv/bin/python manage.py test sales` and `node --test tests/js/*.test.js` → PASS.
- [ ] **Step 5: Commit** `feat(sales): number check page with refresh and check buttons`.

### Task 5: Docs and full verification

**Files:** `README.md` (providers section: number info), `DEPLOY_COOLIFY.md` (operator note: «فحص الأرقام» page, migration `providers 0006` choices-only, worker must be deployed for checks to run).

- [ ] **Step 1:** Update docs.
- [ ] **Step 2:** `.venv/bin/python manage.py test` → OK; `node --test tests/js/*.test.js` → pass; `makemigrations --check --dry-run` → "No changes detected".
- [ ] **Step 3: Commit** `docs: sky number info`.
- [ ] **Step 4 (controller, after the user's go-ahead):** back up the production DB, merge, push, deploy web then worker, smoke: check 0525737739 through the real page flow as a staff user and confirm the card + one operations row.
