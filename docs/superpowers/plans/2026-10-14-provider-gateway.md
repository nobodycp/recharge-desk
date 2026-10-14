# Provider Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move all telecom-company integration into an isolated `providers` app with a shared contract, run provider work in a Celery worker with a Redis-locked single login per provider, make customers wait (never error) while Sky logs in, and add "Charge & record sale" plumbing.

**Architecture:** Modular monolith. `providers` owns adapters (one package per company, own logic), encrypted accounts, Redis session store + login lock, and a sync `service` API. Consuming apps own their jobs/tasks: `phone_refresh/tasks.py` (refresh), `sales/tasks.py` (charge), `sms_gateway` completion hook. Web requests only enqueue and poll.

**Tech Stack:** Django 4.2, Python 3.9 (local/CI) + 3.12 (prod), Celery 5.4 + Redis, `cryptography` (Fernet), `fakeredis` (dev/tests), HTMX + vanilla JS.

**Spec:** `docs/superpowers/specs/2026-10-14-provider-gateway-design.md` — read the "Revisions after codebase review" section first; it supersedes earlier sections.

## Global Constraints

- Python 3.9 compatible: no `StrEnum`, no `match`, keep `from __future__ import annotations` at top of new modules.
- Dependencies added to `requirements.txt` exactly: `celery[redis]>=5.4,<5.6`, `redis>=5,<6`, `cryptography>=42,<45`, `fakeredis>=2.23,<3`.
- Tests use Django `TestCase` and run with `.venv/bin/python manage.py test <label>`; full suite must stay green (baseline: 446 tests OK).
- New env vars: `REDIS_URL`, `PROVIDER_SECRETS_KEY`, `REFRESH_ASYNC_ENABLED` (default `1`).
- No other app imports `providers.adapters.*`; only `providers.service`, `providers.contracts`, `providers.registry`, `providers.jobs`, `providers.health`.
- Customer-facing text comes from `CustomerMessage` rows; never return exception text, provider internals, or credentials to a customer.
- Secrets (passwords, TOTP, JWT, session ids, cookies) never appear in logs, `ProviderJob.result`, `RefreshLog.raw_body`, or templates.
- UI follows `docs/DESIGN_GUIDE.md` and existing management/employee templates (same components, RTL-safe, Arabic strings via `{% trans %}`/`gettext` + `tools/fill_ar_translations.py`).
- Customer max wait default `90` s (`SystemSettings.refresh_wait_seconds`); sync API wait `45` s; refresh retries max `3`; status poll every `2` s; keep-alive every `5` min; unverified charge check every `2` min, escalate after `30` min; refresh job retention `30` days.

## Review Focus

- Same phone submitted twice quickly (double tap / two tabs) → one provider call, both pollers get the same result (Task 6 test `test_inflight_job_reused`).
- Redis/worker down at enqueue → customer gets `busy` message, HTTP 200, no 500 (Task 6 test `test_enqueue_failure_returns_busy`).
- Charge form submitted twice (double click / browser resubmit) → one sale, one job (Task 10 test `test_duplicate_form_token_returns_existing_sale`).
- Sky session expires while 5 refreshes are in flight → exactly one login (Task 4 test `test_concurrent_auth_required_logs_in_once`).
- Charge provider timeout after request was sent → `unverified`, no reversal, no retry (Task 10 test `test_transient_after_send_is_unverified_without_reversal`).

---

### Task 1: Infrastructure — Celery, Redis client, `providers` app skeleton

**Files:**
- Modify: `requirements.txt`, `config/settings/base.py`, `config/settings/production.py`, `config/__init__.py`, `.github/workflows/test.yml`
- Create: `config/celery.py`, `providers/__init__.py`, `providers/apps.py`, `providers/redis_conn.py`, `providers/tests/__init__.py`, `providers/tests/test_infra.py`

**Interfaces:**
- Produces: `providers.redis_conn.get_redis() -> redis.Redis` (real client from `REDIS_URL`, else process-wide `fakeredis.FakeRedis` singleton); `config.celery.app` (Celery app, autodiscovers tasks); settings `REDIS_URL: str`, `REFRESH_ASYNC_ENABLED: bool`, `CELERY_TASK_ALWAYS_EAGER = not REDIS_URL`, `CELERY_TASK_ROUTES` mapping `phone_refresh.tasks.*`→`refresh`, `sales.tasks.*`→`charge`, `providers.tasks.*`→`refresh`.

- [ ] **Step 1: Write failing tests** in `providers/tests/test_infra.py`:
  - `test_get_redis_without_url_returns_working_fake`: `get_redis().set("k","v")`; `get_redis().get("k") == b"v"`; two calls return the same object.
  - `test_celery_eager_without_redis_url`: `from config.celery import app`; `app.conf.task_always_eager is True` under test settings.
- [ ] **Step 2: Run** `.venv/bin/python manage.py test providers.tests.test_infra` → FAIL (module missing).
- [ ] **Step 3: Implement.** Add deps; `pip install -r requirements.txt` into `.venv`. `config/celery.py` standard Django pattern (`Celery("recharge_desk")`, `config_from_object("django.conf:settings", namespace="CELERY")`, `autodiscover_tasks()`); `config/__init__.py` imports `app as celery_app`. In `base.py`: add `"providers"` to `INSTALLED_APPS`; read `REDIS_URL`; `CELERY_BROKER_URL = REDIS_URL or "memory://"`; `CELERY_RESULT_BACKEND = None` (we store results in `ProviderJob`); when `REDIS_URL` set, `CACHES["default"]` = `django.core.cache.backends.redis.RedisCache`. `REFRESH_ASYNC_ENABLED = env != "0"`. In `production.py` add `PROVIDER_SECRETS_KEY = _require("PROVIDER_SECRETS_KEY")` (test branch: fixed Fernet test key). Add `PROVIDER_SECRETS_KEY` dummy Fernet key to the CI "System checks" env.
- [ ] **Step 4: Run** the two tests → PASS; run full suite → 446 + 2 OK.
- [ ] **Step 5: Commit** `feat(providers): add Celery/Redis infrastructure and providers app`.

### Task 2: Contract, registry, and moving adapters (behaviour unchanged)

**Files:**
- Create: `providers/contracts.py`, `providers/registry.py`, `providers/adapters/__init__.py`, `providers/adapters/{sky,layan,aloha,areen}/__init__.py`, `providers/adapters/sky/client.py` (moved from `phone_refresh/providers/sky_sales_client.py`), `providers/adapters/{sky,layan,aloha,areen}/adapter.py`
- Move tests: `phone_refresh/tests_sky_provider.py`, `tests_sky_sales_client.py`, `tests_aloha_provider.py` → `providers/tests/test_sky_adapter.py`, `test_sky_client.py`, `test_aloha_adapter.py` (update patch targets only)
- Modify: `phone_refresh/services/pattern_matcher.py`, `phone_refresh/services/refresh_service.py`, `companies/views.py:170`, `companies/sky_reconcile.py:713`
- Delete: `phone_refresh/providers/` (whole package, incl. empty `captcha/`)

**Interfaces:**
- Produces in `providers/contracts.py`:
  - `RawResponse` (moved verbatim from `phone_refresh/providers/base.py`).
  - `class Capability(str, Enum)`: `REFRESH="refresh"`, `CHARGE="charge"`, `CHECK_CHARGE="check_charge"`, `RECONCILE="reconcile"`.
  - `class ChargeResult(str, Enum)`: `SUCCESS="success"`, `FAILED="failed"`, `UNVERIFIED="unverified"`.
  - `@dataclass ChargeContext(sale_id: int, phone: str, expected_cost: Decimal, product_name: str, is_esim: bool, idempotency_key: str)`.
  - `@dataclass ChargeOutcome(result: ChargeResult, provider_ref: str = "", plan_name: str = "", provider_cost: Decimal | None = None, message: str = "", raw_excerpt: str = "")`.
  - Errors: `ProviderError(Exception)`; `AuthRequired(ProviderError)`; `TransientError(ProviderError)`; `PermanentError(ProviderError)`.
  - `class BaseAdapter`: attrs `key: str`, `capabilities: frozenset`, `timeout: int = 20`, `needs_login: bool = False`, `reconcile_template: str = ""`; methods raising `NotImplementedError` by default: `refresh(phone) -> RawResponse`, `charge(ctx) -> ChargeOutcome`, `check_charge(ctx, provider_ref) -> ChargeOutcome`, `reconcile(company, period_from, period_to, **opts)`, `login() -> None`, `ping() -> None`.
- Produces in `providers/registry.py`: `ADAPTERS: dict[str, type[BaseAdapter]]`; `get_adapter(key: str) -> BaseAdapter` (raises `KeyError`); `supports(key: str, capability: Capability) -> bool`.
- Adapters: `SkyAdapter`, `LayanAdapter`, `AlohaAdapter`, `AreenAdapter`; `refresh(phone)` = old `call(phone)` body. All declare `{Capability.REFRESH}` for now.

- [ ] **Step 1: Move the three provider test modules** to `providers/tests/`, rename `.call(` → `.refresh(` and patch targets to `providers.adapters.sky.adapter.execute_refresh`, `providers.adapters.sky.client.*`, `providers.adapters.aloha.adapter.requests.Session`. Add `providers/tests/test_registry.py`: `test_get_adapter_known_keys` (`get_adapter("sky").key == "sky"` for all four), `test_get_adapter_unknown_raises_keyerror`, `test_supports_refresh_true_charge_false_for_sky`.
- [ ] **Step 2: Run** `manage.py test providers` → FAIL (imports).
- [ ] **Step 3: Implement** contracts/registry; `git mv` client and adapters; update imports in `pattern_matcher.py`, `refresh_service.py` (`get_provider(key).call` → `get_adapter(key).refresh`), `companies/views.py`, `companies/sky_reconcile.py`. Delete `phone_refresh/providers/`.
- [ ] **Step 4: Run** `manage.py test providers phone_refresh companies sales sms_gateway` then full suite → all OK.
- [ ] **Step 5: Commit** `refactor(providers): move provider adapters behind a shared contract`.

### Task 3: Encrypted provider accounts + admin account card

**Files:**
- Create: `providers/crypto.py`, `providers/models.py` (`ProviderAccount`), `providers/accounts.py`, `providers/migrations/0001_initial.py`, `providers/migrations/0002_import_sky_env.py`, `providers/forms.py`, `providers/tests/test_accounts.py`
- Modify: `providers/adapters/sky/client.py` (credentials source; delete `_sky_env_paths`, `load_dotenv_sky`, `sky_lab` reference), `phone_refresh/views/settings.py` (`providers_index` context + new POST view `provider_account_save`), `phone_refresh/urls.py`, `phone_refresh/templates/phone_refresh/_provider_tab.html`

**Interfaces:**
- Produces: `providers.crypto.encrypt(plain: str) -> str`, `decrypt(token: str) -> str` (Fernet, key `settings.PROVIDER_SECRETS_KEY`; dev fallback derives a key from `SECRET_KEY`).
- `ProviderAccount`: `provider_key` (unique, choices = registry keys), `is_enabled`, `username`, `secret_encrypted`, `totp_secret_encrypted`, `extra_encrypted` (JSON string: `{"proxy_url": ...}`), `last_login_at`, `last_login_ok` (nullable bool), `last_error` (≤500 chars, sanitised), `updated_at`.
- `providers.accounts.Credentials(username: str, password: str, totp_secret: str, extra: dict)`; `get_credentials(provider_key: str) -> Credentials` — DB account first, else Sky env fallback (`SKY_SALES_USER`, `SKY_SALES_PASSWORD`, `SKY_SALES_TOTP_SECRET` or secret from `SKY_SALES_OTP_AUTH_URI`, `SKY_SALES_PROXY`), else raises `PermanentError("config_error")`. `record_login(provider_key, ok: bool, error: str = "")`.
- `ProviderAccountForm`: `username`, `password` (blank = keep), `totp_secret` (blank = keep; accepts raw secret or `otpauth://` URI), `proxy_url` (blank = keep), `is_enabled`. Never renders stored values for secret fields.

- [ ] **Step 1: Write failing tests** `providers/tests/test_accounts.py`:
  - `test_encrypt_roundtrip_and_ciphertext_differs`.
  - `test_get_credentials_prefers_db_account_over_env` (env set via `override_settings`/`mock.patch.dict(os.environ)`).
  - `test_get_credentials_env_fallback_for_sky`.
  - `test_get_credentials_missing_raises_permanent_config_error`.
  - `test_blank_password_keeps_existing_secret` (form save twice, second with blank password, `decrypt(secret_encrypted)` unchanged).
  - `test_account_card_never_renders_secret` (management client GETs `phone_refresh:providers_index?tab=sky`; response does not contain the plaintext password or TOTP secret).
  - `test_otpauth_uri_is_reduced_to_secret`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Data migration `0002` reads Sky env vars and creates the `sky` account only if env has user+password and no row exists. Sky client gets credentials only via `get_credentials("sky")`. Account card on the Sky tab shows username, enabled, last login time/result, and form; POST `provider_account_save` (management only, CSRF) redirects back to `?tab=<key>`.
- [ ] **Step 4: Run** `manage.py test providers phone_refresh` → OK.
- [ ] **Step 5: Commit** `feat(providers): encrypted provider accounts with write-only admin form`.

### Task 4: Redis session store, login lock, `service` sync API, keep-alive

**Files:**
- Create: `providers/sessions.py`, `providers/service.py`, `providers/sanitize.py`, `providers/tasks.py`, `providers/health.py`, `providers/tests/test_sessions.py`, `providers/tests/test_service.py`
- Modify: `providers/adapters/sky/client.py` (session persistence via `SessionStore("sky")`; remove `/tmp` file code, `session_file_path`, `client_from_env`, `execute_refresh`'s internal re-login), `providers/adapters/sky/adapter.py`, `config/settings/base.py` (`CELERY_BEAT_SCHEDULE`), `phone_refresh/templates/phone_refresh/_providers_general_tab.html` (health card)

**Interfaces:**
- `providers.sessions.SessionStore(provider_key)`: `load() -> dict | None`, `save(data: dict) -> None` (TTL 12 h), `clear() -> None`, `generation() -> int`, `bump_generation() -> int`. Keys: `provider:session:<key>`, `provider:session-gen:<key>`.
- `providers.sessions.run_authenticated(adapter: BaseAdapter, fn: Callable[[], T]) -> T`: capture `gen = generation()`; call `fn()`; on `AuthRequired` acquire `get_redis().lock(f"provider:login-lock:{key}", timeout=180, blocking_timeout=150)`; inside, if `generation() == gen` call `adapter.login()` then `bump_generation()` and `accounts.record_login(key, True)` (on login exception: `record_login(key, False, sanitized)` and re-raise as `TransientError("login_failed")`); release; call `fn()` once more (a second `AuthRequired` propagates). Lock timeout → `TransientError("login_lock_timeout")`.
- `providers.sanitize.scrub(text: str) -> str` removes values of keys `password|jwtToken|sessionId|otpCode|cookie|token` in JSON/query/form text and replaces them with `***`.
- `providers.service`:
  - `refresh(provider_key: str, phone: str) -> RawResponse` — adapter via registry; `run_authenticated`; retries `TransientError` up to 3 attempts total with sleeps `1, 2` s; final failure → `RawResponse(text="", json=None, status_code=0, error=<sanitised>)`; `PermanentError` → same with error string.
  - `charge(provider_key: str, ctx: ChargeContext) -> ChargeOutcome` — `run_authenticated`, no retry; `PermanentError` → `FAILED`; `TransientError`/any other exception → `UNVERIFIED`. (`AuthRequired` reaches here only before send — adapter contract.)
  - `check_charge(provider_key, ctx, provider_ref) -> ChargeOutcome` — `run_authenticated`; exceptions → `UNVERIFIED`.
  - `reconcile(company, period_from, period_to, **opts) -> tuple[object, str]` — added in Task 9.
  - `keepalive(provider_key: str) -> None` — `run_authenticated(adapter, adapter.ping)`.
- `providers.tasks.keepalive_sessions()` (beat every 300 s): for each enabled `ProviderAccount` whose adapter `needs_login`, call `service.keepalive`; log, never raise.
- `providers.health.status() -> dict` with `redis_ok: bool`, `worker_ok: bool` (`app.control.ping(timeout=1)` non-empty; `True` when eager), `accounts: list[{key, last_login_at, last_login_ok}]`.
- Sky adapter: `needs_login = True`; `refresh(phone)` loads client from store (raise `AuthRequired` if session not ready), maps `SkySalesSessionError` → `AuthRequired`, `requests` errors → `TransientError`, returns the same JSON `RawResponse` shape as today (reasons `success|not_found|not_active|not_eligible`); `login()` = `force_new_session(creds...)` with TOTP from `Credentials.totp_secret`, saves to store; `ping()` = `_probe_session()`.

- [ ] **Step 1: Write failing tests** `providers/tests/test_sessions.py` (use a `FakeAdapter(BaseAdapter)` with `needs_login=True`, counting `login()` calls):
  - `test_auth_required_triggers_single_login_and_retry` (fn raises `AuthRequired` first call, returns `"ok"` second; result `"ok"`, `login_calls == 1`).
  - `test_concurrent_auth_required_logs_in_once`: 5 threads; fn raises `AuthRequired` until generation > start; `login()` sleeps 0.2 s; assert `login_calls == 1` and all 5 results `"ok"`.
  - `test_second_auth_required_propagates`.
  - `test_login_failure_becomes_transient_and_records_account_error` (`ProviderAccount.last_login_ok is False`, `last_error` has no password).
  `providers/tests/test_service.py`:
  - `test_refresh_retries_transient_three_times_then_error_response` (`status_code == 0`, `error` set, 3 calls; patch `time.sleep`).
  - `test_charge_permanent_error_is_failed`, `test_charge_transient_error_is_unverified`.
  - `test_scrub_removes_password_and_jwt`.
  - `test_sky_refresh_without_session_raises_auth_required` and `test_sky_session_saved_in_store_not_tmp` (patch Sky HTTP calls; assert no file at old `/tmp/.sky_sales_session.json` path is written and `SessionStore("sky").load()["jwt_token"]` set).
- [ ] **Step 2: Run** `manage.py test providers` → FAIL.
- [ ] **Step 3: Implement** per interfaces; beat schedule entry `providers-keepalive` every 300 s; health card on providers general tab (Redis / worker / last login per account).
- [ ] **Step 4: Run** `manage.py test providers` then full suite → OK.
- [ ] **Step 5: Commit** `feat(providers): Redis session store with single-login lock and keep-alive`.

### Task 5: `ProviderJob` + refresh service split + refresh task

**Files:**
- Create: `providers/jobs.py`, `providers/migrations/0003_providerjob.py`, `phone_refresh/tasks.py`, `phone_refresh/services/refresh_jobs.py`, `phone_refresh/migrations/0019_refresh_wait_and_async_statuses.py`, `phone_refresh/tests_refresh_jobs.py`
- Modify: `providers/models.py`, `phone_refresh/services/refresh_service.py`, `phone_refresh/models.py` (`SystemSettings.refresh_wait_seconds`), `phone_refresh/forms.py` + `_general_tab.html` (expose the new setting)

**Interfaces:**
- `ProviderJob` fields: `id` UUID4 pk, `kind` (`refresh|charge|check_charge`), `provider_key`, `phone`, `status` (`queued|running|done|failed`), `source`, `idempotency_key` (unique, null), `payload` JSON, `result` JSON, `attempts`, `created_at`, `started_at`, `finished_at`. Index `(kind, phone, status, created_at)`.
- `providers.jobs`: `create_job(*, kind, provider_key, phone, source, payload=None, idempotency_key=None) -> ProviderJob`; `mark_running(job) -> bool` (atomic `queued→running`, returns False if already taken); `mark_done(job, result: dict)`; `mark_failed(job, result: dict)`; `find_inflight(kind, phone, max_age_seconds) -> ProviderJob | None`.
- `phone_refresh.services.refresh_service`:
  - `precheck(phone, *, ip, internal_test=False, forced_provider=None, bypass_provider_off=False, source) -> tuple[RefreshResult | None, str]` — steps 1–4b of today's `refresh_phone` (service off, validation, cooldown, routing, provider off); returns an immediate (already logged) result or the provider key.
  - `complete(*, phone, provider_key, raw, ip, source, started, internal_test=False) -> RefreshResult` — match + `_record_and_return` (raw passed through `scrub` before persisting).
  - `refresh_phone(...)` keeps its signature: `precheck` → `providers.service.refresh` → `complete`.
  - `result_payload(result: RefreshResult) -> dict` — the JSON shape currently built in `public.py` / `_refresh_json_payload` (status, message, optional last-refresh fields). Both callers switch to it.
- `phone_refresh.services.refresh_jobs`:
  - `@dataclass RefreshTicket(job_id: str | None, payload: dict | None)`.
  - `submit_refresh(phone, *, ip, source, internal_test=False, forced_provider=None, bypass_provider_off=False, notify: dict | None = None) -> RefreshTicket` — if `not settings.REFRESH_ASYNC_ENABLED` → `RefreshTicket(None, result_payload(refresh_phone(...)))`; else `precheck`; immediate → ticket with payload; else `find_inflight("refresh", phone, refresh_wait_seconds)` → reuse; else `create_job` (payload holds ip/source/flags/notify) and `transaction.on_commit(lambda: run_refresh_job.delay(str(job.id)))`; enqueue exceptions (`kombu.exceptions.OperationalError`, `redis.exceptions.ConnectionError`, `OSError`) → payload of `busy` status (logged via `_record_and_return` with error `enqueue_failed`).
  - `job_payload(job_id: str) -> dict` — unknown id → `{"status": "error", "message": {"title": "خطأ", "body": "انتهت صلاحية الطلب، أعد المحاولة."}}`; queued/running and age < `refresh_wait_seconds` → `{"status": "processing", "job": id}`; older → `processing` status message payload (status `"processing"`, plus `"final": true`); done → `job.result`.
  - `wait_for_job(job_id, timeout_seconds=45) -> dict` — polls `job_payload` every 0.5 s until not processing or timeout; returns last payload.
- `phone_refresh.tasks.run_refresh_job(job_id: str)` — `mark_running` (skip if False); `providers.service.refresh`; `complete`; `mark_done(job, result_payload(result))`; then if `payload["notify"]` has `sms_inbound_id` call `sms_gateway.services.processing.complete_inbound(inbound_id, status_code, title, body)` (Task 8). Unexpected exception → `mark_failed` with the `error` status payload.
- Migration `0019`: field `refresh_wait_seconds` default 90; `RefreshStatus` rows `processing` ("قيد المعالجة") and `busy` ("الخدمة مشغولة") with `CustomerMessage` bodies "طلبك قيد المعالجة، أعد المحاولة بعد دقيقة." and "الخدمة مشغولة حالياً، حاول بعد قليل."
- Periodic task `providers.tasks.purge_old_jobs` (daily): delete `kind=refresh` jobs older than 30 days.

- [ ] **Step 1: Write failing tests** `phone_refresh/tests_refresh_jobs.py` (eager Celery; patch `providers.service.refresh` to return Sky-shaped `RawResponse`):
  - `test_submit_returns_job_and_task_completes_with_refreshed_payload` (`job_payload(id)["status"] == "refreshed"`, one `RefreshLog`).
  - `test_precheck_short_circuit_returns_payload_without_job` (invalid phone → `ticket.job_id is None`, payload status `not_found`).
  - `test_async_disabled_runs_sync` (`override_settings(REFRESH_ASYNC_ENABLED=False)`; payload present, `ProviderJob.objects.count() == 0`).
  - `test_job_payload_processing_then_deadline_message` (job queued, `created_at` moved back 91 s → status `processing` with the seeded body text and `final` true).
  - `test_unknown_job_returns_generic_error`.
  - `test_refresh_phone_sync_signature_unchanged` (existing callers' kwargs still work).
  - `test_raw_body_is_scrubbed` (provider raw contains `"jwtToken": "abc"`; stored `RefreshLog.raw_body` lacks `abc`).
- [ ] **Step 2: Run** `manage.py test phone_refresh.tests_refresh_jobs` → FAIL.
- [ ] **Step 3: Implement** per interfaces.
- [ ] **Step 4: Run** `manage.py test phone_refresh providers` then full suite → OK.
- [ ] **Step 5: Commit** `feat(phone_refresh): queue refresh jobs and split precheck/complete`.

### Task 6: Public page + JSON API async, status endpoint, Redis rate limit

**Files:**
- Create: `core/ratelimit.py`, `core/tests_ratelimit.py`
- Modify: `phone_refresh/views/public.py`, `phone_refresh/urls.py` (`public_status` route `refresh/status/<uuid:job_id>/`), `phone_refresh/templates/phone_refresh/public_refresh.html` (data attrs: status URL template, wait seconds), `static/phone_refresh/js/refresh.js`, `phone_refresh/tests_public_api.py` (patch target → `phone_refresh.views.public.submit_refresh`)

**Interfaces:**
- `core.ratelimit.allow(bucket: str, ident: str, limit: int, window_seconds: int) -> bool` — fixed window `INCR` + `EXPIRE` on `get_redis()`; `limit == 0` or empty ident → True; Redis errors → True (fail open, logged).
- `public_refresh_api`: replaces in-memory limiter with `allow("refresh-min", ip, per_min, 60)` and `allow("refresh-hour", ip, per_hour, 3600)`; body `"async": true` → `submit_refresh`; with `job_id` → `202 {"status":"processing","job":id}`, else `200 payload`. Without async → `wait_for_job(..., 45)` when a job was created, else payload.
- `public_refresh_status(request, job_id)` GET → `JsonResponse(job_payload(job_id))`, rate limit `allow("refresh-status", ip, 60, 60)` else 429.
- JS: send `async: true`; on 202 poll status URL every 2000 ms until `status !== "processing"` or `final === true` or elapsed > waitSeconds + 10; keep the existing "sending" loader text during polling; final message via existing `buildMessage`. Add `processing`/`busy` to `STATUS_TO_TYPE` as `"info"`/`"warning"`.

- [ ] **Step 1: Write failing tests**
  - `core/tests_ratelimit.py`: `test_allow_blocks_after_limit_in_window`, `test_zero_limit_disables`, `test_redis_error_fails_open`.
  - `phone_refresh/tests_public_api.py` additions: `test_async_request_returns_202_with_job`, `test_status_endpoint_returns_final_payload`, `test_sync_client_gets_final_payload` (no `async` flag → 200 with final status), `test_enqueue_failure_returns_busy` (patch `run_refresh_job.delay` to raise `kombu.exceptions.OperationalError`; HTTP 200, status `busy`), `test_inflight_job_reused` (two async POSTs same phone, provider refresh patched to block via not-yet-run job → same job id, one `ProviderJob`), `test_rate_limit_shared_via_redis` (limit 1/min → second POST 429).
- [ ] **Step 2: Run** `manage.py test core.tests_ratelimit phone_refresh.tests_public_api` → FAIL.
- [ ] **Step 3: Implement**; delete `_rate_state_*` globals and `_check_rate_limit`.
- [ ] **Step 4: Run** same + full suite → OK. Manually: `runserver`, open the public page, submit a valid number with `providers.service.refresh` live disabled (provider off) and with a provider enabled; confirm loader then message.
- [ ] **Step 5: Commit** `feat(phone_refresh): async public refresh with polling and shared rate limits`.

### Task 7: Employee refresh + internal test screens use jobs

**Files:**
- Modify: `sales/views/employee.py` (`employee_refresh_phone`, delete `_refresh_json_payload`), `sales/urls.py` (`employee_refresh_status` → `employee/refresh/status/<uuid:job_id>/`), `templates/sales/employee_refresh_number.html`, `static/js/employee-refresh-number.js`, `phone_refresh/views/general.py`, `phone_refresh/views/settings.py:266` (internal test), `sales/tests.py` (patch target → `sales.views.employee.submit_refresh`)

**Interfaces:**
- Consumes: `submit_refresh(..., source=RefreshSource.EMPLOYEE)` and `job_payload`; internal tests call `submit_refresh(..., internal_test=True, forced_provider=key, bypass_provider_off=True)`.
- Admin internal-test views keep showing raw diagnostics: when a job id is returned they call `wait_for_job(job_id, 45)` and render `job.result` plus `ProviderJob.result["raw_excerpt"]` (store `raw_excerpt` in the job result only for `internal_test` source).

- [ ] **Step 1: Write failing tests** in `sales/tests.py`: `test_employee_refresh_returns_job_then_status_final` (POST → `job` in JSON; GET status → `refreshed`), `test_employee_status_requires_employee_login` (anonymous → redirect/403).
- [ ] **Step 2: Run** `manage.py test sales` → FAIL.
- [ ] **Step 3: Implement**; JS polling mirrors Task 6.
- [ ] **Step 4: Run** `manage.py test sales phone_refresh` + full suite → OK.
- [ ] **Step 5: Commit** `feat(sales): employee and admin refresh use queued jobs`.

### Task 8: SMS gateway completion hook

**Files:**
- Modify: `sms_gateway/models.py` (`InboundSms.State.REFRESHING = "refreshing"`), `sms_gateway/migrations/0003_inboundsms_refreshing_state.py`, `sms_gateway/services/processing.py`, `sms_gateway/services/refresh_client.py`, `sms_gateway/tests.py`

**Interfaces:**
- `refresh_client.call_refresh_api` unchanged for HTTP mode and for `simulate=True`.
- New in-process async path: `processing.process_inbound` (not simulate, no external URL, async enabled) calls `submit_refresh(number, ip=None, source=RefreshSource.SMS, notify={"sms_inbound_id": inbound.pk})`; immediate payload → finish as today; job → inbound state `refreshing`, `extracted_number` saved, return `ProcessResult(state="refreshing")`.
- `processing.complete_inbound(inbound_id: int, status_code: str, title: str, body: str) -> None` — builds reply via `_reply_for`, applies global cap, sets `PROCESSED`, creates `OutboundSms` (same as `finish`). Idempotent: does nothing unless inbound state is `refreshing`.

- [ ] **Step 1: Write failing tests**: `test_inbound_enqueues_and_reply_created_on_completion` (eager: after `process_inbound`, one `OutboundSms` with the refreshed message, inbound `PROCESSED`), `test_complete_inbound_is_idempotent`, `test_simulate_stays_synchronous`.
- [ ] **Step 2: Run** `manage.py test sms_gateway` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `manage.py test sms_gateway` + full suite → OK.
- [ ] **Step 5: Commit** `feat(sms_gateway): reply to inbound SMS when the refresh job completes`.

### Task 9: Move reconciliation into adapters; explicit company → provider mapping

**Files:**
- Create: `providers/shared/__init__.py`, `providers/shared/phones.py` (`norm_phone`), `providers/shared/rd_sales.py` (`rd_sales_by_phone`, `rd_ledger_totals` — moved from `_rd_sales_by_phone`, `_rd_ledger_totals`), `providers/shared/activity.py` (`PhoneActivityDetail`, `PhoneActivityBreakdown`, `SettlementCycleDetail`, `OrphanRefundDetail`, `UnpairedRechargeDetail`, `phone_activity_breakdown` — moved, used by both), `providers/adapters/layan/reconcile.py` (from `companies/layan_reconcile.py`), `providers/adapters/sky/reconcile.py` (from `companies/sky_reconcile.py`), `companies/migrations/0012_fill_provider_from_name.py`
- Move tests: `companies/tests_sky_reconcile.py` → `providers/tests/test_sky_reconcile.py`; Layan reconcile tests from `companies/tests.py` → `providers/tests/test_layan_reconcile.py`
- Modify: `companies/views.py`, `providers/service.py` (`reconcile`), adapters (`RECONCILE` capability, `reconcile_template`), `phone_refresh/services/refresh_service.py` (delete `COMPANY_TO_PROVIDER` name fallback)
- Delete: `companies/layan_reconcile.py`, `companies/sky_reconcile.py`

**Interfaces:**
- `LayanAdapter.reconcile(company, period_from, period_to, *, workbook, min_amount_diff, pending_credits_text) -> LayanReconcileResult`, `reconcile_template = "companies/layan_reconcile.html"`.
- `SkyAdapter.reconcile(company, period_from, period_to, *, min_amount_diff) -> SkyReconcileResult` (fetch via client inside `run_authenticated`), `reconcile_template = "companies/sky_reconcile.html"`.
- `providers.service.reconcile(company, period_from, period_to, **opts) -> tuple[object, str]` — adapter from `company.phone_refresh_provider`; `KeyError`/no capability → `PermanentError("reconcile_not_supported")`; `AuthRequired` handled by `run_authenticated`.
- `providers.service.company_supports(company, capability) -> bool` replaces `company_supports_sky_reconcile` / `company_supports_layan_reconcile` (uses only `company.phone_refresh_provider`).
- Migration `0012`: for companies with empty `phone_refresh_provider`, set it from the old name rules (`sky`/`سكاي`→`sky`, `aloha`, `layan`, `areen`).

- [ ] **Step 1: Move tests** with import updates only; add `test_company_supports_uses_field_not_name` (company named "Sky Mobile" with empty field → False; field `sky` → True) and `test_fill_provider_migration_maps_names` (call the migration function on fixtures).
- [ ] **Step 2: Run** `manage.py test providers companies` → FAIL.
- [ ] **Step 3: Implement** moves (`git mv`), keep logic byte-for-byte except imports/renamed private helpers; views call `providers.service.reconcile` and render the returned template.
- [ ] **Step 4: Run** `manage.py test providers companies phone_refresh` + full suite → OK (moved reconcile tests unchanged in assertions).
- [ ] **Step 5: Commit** `refactor(providers): reconciliation lives in each provider adapter`.

### Task 10: Charge & record sale — backend

**Files:**
- Create: `sales/charging.py`, `sales/tasks.py`, `sales/migrations/0017_sale_charge_fields.py`, `sales/tests_charging.py`, `providers/tests/fakes.py` (`FakeChargeAdapter`)
- Modify: `sales/models.py`, `config/settings/base.py` (beat: `sales-verify-charges` every 120 s)

**Interfaces:**
- `Sale` fields: `entry_mode` (`manual` default | `auto_charge`), `charge_status` (`""|processing|success|failed|unverified`, indexed), `charge_job_id` (UUID null), `charge_provider_ref`, `charge_plan_name`, `charge_provider_cost` (Decimal null), `charge_checked_at` (null), `charge_message` (≤255). Property `charge_cost_mismatch -> bool` (`charge_provider_cost is not None and != cost_price_snapshot`).
- `sales.charging.can_charge(company) -> bool` — `providers.service.company_supports(company, Capability.CHARGE)` and account enabled.
- `sales.charging.create_charged_sale(*, form_token: str, user, **create_sale_kwargs) -> Sale` — in one transaction: if a `ProviderJob` with `idempotency_key=form_token` exists → return its sale (`Sale.objects.get(charge_job_id=job.id)`); else `create_sale(...)`, `finalize_sale_after_entry` as today, set `entry_mode=auto_charge`, `charge_status=processing`, `create_job(kind="charge", provider_key=..., phone=sale.reference_number, source="employee", payload={"sale_id": sale.pk}, idempotency_key=form_token)`, set `charge_job_id`, `on_commit(run_charge_job.delay)`; enqueue failure → leave `processing` (verify task will pick it up) and log.
- `sales.charging.apply_outcome(sale_id: int, outcome: ChargeOutcome, *, actor=None) -> Sale` — `SUCCESS` → status/ref/plan/cost/message; `FAILED` → `charge_status=failed` then `cancel_sale(sale=..., user=actor)` unless already cancelled; `UNVERIFIED` → `charge_status=unverified`. Sets `charge_checked_at`. Audit via `audit.services.record` with `extra={"charge_status": ...}`.
- `sales.tasks.run_charge_job(job_id: str)` — `mark_running`; build `ChargeContext` from sale; `providers.service.charge`; `apply_outcome`; `mark_done`.
- `sales.tasks.verify_unverified_charges()` — sales with `charge_status in (processing older than 5 min, unverified)` and `charge_provider_ref` or job present: `providers.service.check_charge`; `SUCCESS`/`FAILED` → `apply_outcome`; still unverified → leave (management list shows those older than 30 min as "needs verification").
- `sales.charging.manual_resolve(sale, *, success: bool, user) -> Sale` and `sales.charging.recheck(sale) -> None` (enqueue check) for management actions.

- [ ] **Step 1: Write failing tests** `sales/tests_charging.py` with `FakeChargeAdapter` registered under key `"fake"` via `mock.patch.dict(ADAPTERS, ...)` and a company whose `phone_refresh_provider="fake"`:
  - `test_success_sets_fields_and_keeps_deduction` (company balance decreased by cost once; `charge_status == "success"`; ref stored).
  - `test_failed_cancels_sale_and_restores_balance_once` (balance back to start; one `REVERSAL` ledger row; calling `apply_outcome` FAILED again does not add another).
  - `test_transient_after_send_is_unverified_without_reversal` (adapter raises `TransientError` → `unverified`; balance still deducted; no reversal row).
  - `test_verify_task_resolves_unverified_to_success`.
  - `test_duplicate_form_token_returns_existing_sale` (two calls same token → one `Sale`, one `ProviderJob`).
  - `test_cost_mismatch_flag`.
  - `test_can_charge_false_for_refresh_only_provider` (`sky` today).
- [ ] **Step 2: Run** `manage.py test sales.tests_charging` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `manage.py test sales` + full suite → OK.
- [ ] **Step 5: Commit** `feat(sales): charge-and-record sale backend with safe reversal`.

### Task 11: Charge & record sale — UI markers and management actions

**Files:**
- Modify: `sales/views/employee.py` (`employee_entry`: second submit button `name="action" value="charge"`, hidden `form_token` generated per render), `templates/sales/employee_entry.html`, `templates/sales/employee_recent.html` + `templates/sales/partials/employee_recent_actions.html`, `templates/sales/partials/management_sale_list_results.html`, `sales/forms.py` (`ManagementSaleFilterForm`: `entry_mode`, `charge_status` filters incl. `needs_verification`), `sales/views/management.py` (filters + two POST views), `sales/urls.py`, `tools/fill_ar_translations.py`
- Create: `templates/sales/partials/charge_badge.html`, `sales/tests_charge_ui.py`

**Interfaces:**
- Consumes `can_charge`, `create_charged_sale`, `manual_resolve`, `recheck` (Task 10).
- Routes: `sales:sale_charge_recheck` (POST, management), `sales:sale_charge_resolve` (POST, management, `result=success|failed`).
- `charge_badge.html` renders the "شحن تلقائي" badge + chip: processing "جارٍ الشحن", success "تم الشحن", failed "فشل الشحن", unverified "بحاجة تحقق", plus "فرق تكلفة" when `charge_cost_mismatch`. Rows with `processing` include `hx-get` self-refresh every 5 s (row partial endpoint `sales:sale_charge_row`).

- [ ] **Step 1: Write failing tests**: `test_charge_button_hidden_when_company_cannot_charge`, `test_charge_button_shown_for_fake_provider`, `test_charge_submit_creates_auto_charge_sale`, `test_recent_entries_show_badge_and_status`, `test_management_filter_by_charge_status`, `test_resolve_failed_requires_management_and_reverses` (employee POST → 403; management POST → sale cancelled).
- [ ] **Step 2: Run** `manage.py test sales.tests_charge_ui` → FAIL.
- [ ] **Step 3: Implement**; add Arabic strings to `tools/fill_ar_translations.py`, run `makemessages -l ar --ignore=.venv`, the fill script, `compilemessages`.
- [ ] **Step 4: Run** `manage.py test sales` + full suite → OK; manual check in browser (employee entry with fake provider via shell, recent list chips, management filters) light + dark, AR + EN, mobile width.
- [ ] **Step 5: Commit** `feat(sales): charge button, status badges, and management resolve actions`.

### Task 12: `/api/v1/` refresh endpoints with token scopes

**Files:**
- Create: `providers/api/__init__.py`, `providers/api/urls.py`, `providers/api/views.py`, `phone_refresh/migrations/0020_apitoken_scopes.py`, `providers/tests/test_api_v1.py`
- Modify: `phone_refresh/models.py` (`ApiToken.scopes` CharField, comma list, default `"refresh"`), `phone_refresh/forms.py` + `api_token_form.html` (scope checkboxes), `config/urls.py` (include `providers.api.urls` outside `i18n_patterns` at `api/v1/`)

**Interfaces:**
- `POST /api/v1/refresh/` — Bearer token required (no anonymous mode), scope `refresh`, per-token `allow("api-token", token.pk, 60, 60)`; body `{"phone": "..."}` → `submit_refresh(source=RefreshSource.API)`; `202 {"job": id, "status": "processing"}` or `200 payload`.
- `GET /api/v1/refresh/<uuid>/` — same token auth → `job_payload`.
- `ApiToken.has_scope(scope: str) -> bool`.

- [ ] **Step 1: Write failing tests**: `test_missing_token_401`, `test_token_without_scope_403`, `test_refresh_returns_job_then_status`, `test_per_token_rate_limit_429`, `test_csrf_not_required_for_bearer_api`.
- [ ] **Step 2: Run** `manage.py test providers.tests.test_api_v1` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** + full suite → OK.
- [ ] **Step 5: Commit** `feat(providers): versioned refresh API with scoped tokens`.

### Task 13: Deployment, docs, clean-up

**Files:**
- Modify: `entrypoint.sh` (if `$1 = worker` → `exec celery -A config worker -B -Q refresh,charge,reports --concurrency ${CELERY_CONCURRENCY:-4} --loglevel INFO`; web path unchanged), `Dockerfile` (`ENTRYPOINT ["./entrypoint.sh"]`, `CMD ["web"]`), `DEPLOY_COOLIFY.md` (Redis service, worker service command `worker`, env vars `REDIS_URL`, `PROVIDER_SECRETS_KEY` + how to generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`, `REFRESH_ASYNC_ENABLED`, rollout steps from spec), `.env.example`, `.dockerignore` (add `*.dump`, `*.json.gz`, `*.tgz`, `reports/*.csv`), `README.md` (project layout: `providers`)
- Delete: `captcha_lab/` (empty), stage the already-deleted `sky_lab/` files

- [ ] **Step 1: Verify** `rg -n "sky_lab|sky_sales_session|phone_refresh.providers|COMPANY_TO_PROVIDER|layan_reconcile import|sky_reconcile import" --type py` returns only migrations/history-safe hits (none in app code).
- [ ] **Step 2: Implement** the file changes above.
- [ ] **Step 3: Run** `docker build -t rd-test .` then `docker run --rm rd-test sh -c 'python -c "import celery, redis, cryptography"'` → exit 0 (skip if Docker unavailable and say so); `.venv/bin/python manage.py check`; full test suite → OK.
- [ ] **Step 4: Commit** `chore: worker entrypoint, Coolify docs, and provider clean-up`.

### Task 14 (blocked): Sky charge + check_charge adapter

**Blocked on:** a capture (browser DevTools "Save all as HAR" or copied request/response bodies) from the Sky Sales portal of (a) performing one charge/renewal for a number and (b) looking up the resulting task's status. Spec revision 10.

**Files (when unblocked):** `providers/adapters/sky/client.py`, `providers/adapters/sky/adapter.py`, `providers/tests/test_sky_charge.py`, recorded fixtures under `providers/tests/fixtures/sky/`.

**Interfaces:** `SkyAdapter.capabilities` gains `CHARGE`, `CHECK_CHARGE`; `charge(ctx)` raises `AuthRequired` only before the create-task call; after sending, transport errors → `TransientError` (service maps to `UNVERIFIED`); task id `-1`/`ERROR` → `ChargeOutcome(FAILED)`; `check_charge(ctx, ref)` maps Sky task states to `SUCCESS`/`FAILED`/`UNVERIFIED`.

- [ ] Steps follow the same TDD cycle with fixtures from the capture; commit `feat(providers): Sky charge and charge status check`.
