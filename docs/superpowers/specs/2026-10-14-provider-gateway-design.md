# Provider Gateway (بوابة المزوّدين) — Design

**Date:** 2026-10-14  
**Status:** Design approved in chat; awaiting written-spec review  
**Sub-project:** 1 of 5 (see "Roadmap" at the end)

## Problem

Everything that talks to a telecom company (Sky, Layan, Aloha, Areen) is scattered:

- The Sky HTTP client lives in `phone_refresh/providers/sky_sales_client.py`; Sky and Layan
  reconciliation live in `companies/sky_reconcile.py` / `companies/layan_reconcile.py` and import
  across apps.
- A company is mapped to a provider partly by substring-matching its name (`"sky" in name`).
- There is no shared contract for the operations the business needs now and next: refresh,
  charge, check-charge, reconcile.

The concrete production bug: when a customer submits a number on the public refresh link and the
Sky session has expired, the Sky login (password + TOTP + JWT) runs **inside the customer's HTTP
request**. Sky calls use a 90–120 s timeout while gunicorn kills requests at 60 s
(`entrypoint.sh`), so the customer gets an error. The Sky session is also a JSON file in `/tmp`
shared by 3 workers × 2 threads; concurrent requests each start their own login and clobber each
other (Sky also rejects a reused TOTP code).

## Goals

1. One isolated module that is the **only** place the system talks to telecom companies.
2. Uniform **operation names and result shapes**; each company keeps its **own logic** for every
   operation (refresh, charge, check, reconcile differ per company and must not be merged).
3. Customers never see an error caused by provider login; they wait, then get the correct message.
4. Never two concurrent logins to the same provider account.
5. New "Charge & record sale" action in sales entry, with a clear marker and live status for
   employees and management.
6. Adding a new company = adding one adapter package; no changes to pages or sales code.
7. Reusable by the internal system now and by the future online store via `/api/v1/`.

## Non-goals (this sub-project)

- Full-system reorganisation and dead-code removal outside the provider area (sub-project 2).
- Full security audit / hardening of login, admin, etc. (sub-project 3).
- Online store, unified inventory, points of sale (sub-project 4).
- Implementing charge for Layan / Areen / Aloha (sub-project 5) — the contract supports it.
- Multiple accounts per provider (one account per company; model allows more later).
- External third-party API consumers (only internal system + future store).

## Decisions (locked)

| Topic | Choice |
|-------|--------|
| Architecture | Modular monolith; isolated `providers` Django app + Celery worker (approach A) |
| Queue / locks / rate limits | Redis (new Coolify service) |
| Accounts | One account per provider company |
| Consumers | Internal system (public refresh page, employee screens, SMS gateway, management) + future store |
| Logic per company | Owned by each adapter; contract unifies names, result shapes, error types only |
| Charge input | Phone number + sale context; adapter discovers line/plan from the provider itself (no product code) |
| Charge accounting | Deduct at entry (existing `create_sale`), auto-reverse via `cancel_sale` only on **confirmed** failure |
| Unknown charge outcome | Never auto-retry, never auto-reverse; poll `check_charge`, escalate to management |
| Customer max wait | 90 s default, admin-configurable |

## Architecture

```
                 ┌──────────── web (gunicorn) ─────────────┐
 public page ──► │ phone_refresh views ─┐                   │
 employee UI ──► │ sales views ─────────┼─► providers.service ──► enqueue ──► Redis
 SMS gateway ──► │ sms_gateway ─────────┘        ▲          │                  │
 store (later) ► │ providers/api (/api/v1/) ─────┘          │                  ▼
                 └──────────────────────────────────────────┘       ┌── worker (celery) ──┐
                                                                    │ providers.tasks      │
                                                                    │   → registry         │
                                                                    │   → adapter (sky…)   │
                                                                    │   → sessions (lock)  │
                                                                    └──────────────────────┘
```

Same Docker image runs as three Coolify services: `web`, `worker` (Celery worker with embedded
beat), plus `redis`. Shared PostgreSQL as today.

### Package layout

```
providers/
  contracts.py        # ProviderAdapter protocol, result dataclasses, error types
  registry.py         # key → adapter class; capabilities per adapter
  adapters/
    sky/
      client.py       # Sky Sales Portal HTTP client (moved from phone_refresh)
      adapter.py      # refresh / charge / check_charge / reconcile — Sky logic
      reconcile.py    # Sky reconciliation logic (moved from companies/sky_reconcile.py)
    layan/
      adapter.py      # refresh (existing), reconcile
      reconcile.py    # Layan reconciliation logic (moved from companies/layan_reconcile.py)
    aloha/adapter.py  # refresh (existing)
    areen/adapter.py  # refresh (existing)
  shared/
    phones.py         # norm_phone and other genuinely shared helpers
    rd_sales.py       # loaders for our own sales/ledger used by reconcilers
  sessions.py         # Redis session store + distributed login lock
  crypto.py           # Fernet encryption for stored credentials
  models.py           # ProviderAccount, ProviderJob
  service.py          # the ONLY entry point other apps import
  tasks.py            # Celery tasks + periodic tasks
  api/                # /api/v1/ endpoints for the store (token auth)
  admin views/templates for account + queue status
```

Dependency rule: `providers` may import `sales`/`companies` models only inside
`shared/rd_sales.py` and the charge task's accounting step. No other app imports
`providers.adapters.*` directly — only `providers.service`.

## Contract

```python
class Capability(StrEnum):
    REFRESH = "refresh"
    CHARGE = "charge"
    CHECK_CHARGE = "check_charge"
    RECONCILE = "reconcile"

class ProviderAdapter(Protocol):
    key: str                       # "sky", "layan", ...
    capabilities: frozenset[Capability]

    def refresh(self, phone: str) -> RefreshOutcome: ...
    def charge(self, phone: str, ctx: ChargeContext) -> ChargeOutcome: ...
    def check_charge(self, provider_ref: str) -> ChargeOutcome: ...
    def reconcile(self, period_from: date, period_to: date, **opts) -> ReconcileReport: ...
```

Result shapes (uniform across companies):

- `RefreshOutcome(raw: RawResponse, reason: str = "")` — the adapter returns the provider's own
  answer (for Sky: the JSON with `reason` = `success|not_found|not_active|not_eligible`). The
  final `RefreshStatus` is still resolved by the existing admin-editable `ProviderResponseRule`
  matching (e.g. Sky `not_eligible → wait`, `not_active → error` as seeded today), so admins keep
  control of mapping and customer text (`CustomerMessage`). Adapters never pick the final status.
- `ChargeContext(sale_id, expected_cost, product_name, is_esim, idempotency_key)`.
- `ChargeOutcome(status: success|failed|unverified, provider_ref, plan_name, provider_cost,
  message, raw_excerpt)`.
- `ReconcileReport(sections: list[ReconcileSection], summary: dict)` — each adapter defines its
  own sections/categories/columns; the management page renders sections generically.

Error types — the only coupling between adapters and the orchestration:

| Error | Meaning | Orchestrator behaviour |
|-------|---------|------------------------|
| `AuthRequired` | Session missing/expired/rejected | Acquire provider login lock, log in, retry operation once |
| `TransientError` | Network/timeout/5xx, safe to retry | Retry with backoff (max 3) — **not** for `charge` after the request may have reached the provider |
| `PermanentError` | Provider rejected definitively | Stop; map to final status/message |

This keeps the current behaviour of all four providers' refresh mapping unchanged; only where the
code lives and how it is invoked changes.

Company → provider mapping becomes the explicit `Company.phone_refresh_provider` field only
(renamed in UI to "Provider"); the name-substring fallback (`COMPANY_TO_PROVIDER`,
`company_supports_sky_reconcile` name check) is removed with a data migration that fills the
field for existing companies whose names currently match.

## Models

`ProviderAccount` (one row per provider key for now):

- `provider_key` (unique), `is_enabled`
- `username`, `secret_encrypted` (password), `totp_secret_encrypted`, `extra_encrypted` (JSON for
  provider-specific settings, e.g. proxy URL)
- `last_login_at`, `last_login_ok`, `last_error` (sanitised)
- Encryption: Fernet with key from env `PROVIDER_SECRETS_KEY`; values are write-only in the UI.
- Data migration imports current `SKY_SALES_*` env values on first run if present; env remains a
  fallback until the admin saves the account, then env is ignored.

`ProviderJob`:

- `id` (UUID4, also the public tracking id), `kind` (`refresh|charge|check_charge|reconcile`),
  `provider_key`, `phone`, `status` (`queued|running|done|failed`), `source` (web/api/employee/sms/
  store/internal_test), `idempotency_key` (unique, nullable), `sale` (FK, nullable),
  `result` (JSON, sanitised), `attempts`, `created_at`, `started_at`, `finished_at`.
- Retention: periodic task deletes `refresh` jobs older than 30 days (`RefreshLog` remains the
  long-term history as today).

`Sale` additions:

- `entry_mode` (`manual` default | `auto_charge`)
- `charge_status` (blank | `processing` | `success` | `failed` | `unverified`)
- `charge_provider_ref`, `charge_plan_name`, `charge_provider_cost` (nullable decimal),
  `charge_checked_at`
- Property `charge_cost_mismatch` → `charge_provider_cost` differs from `cost_price_snapshot`.

`phone_refresh` additions:

- `SystemSettings.refresh_wait_seconds` (new, default 90).
- Two new `RefreshStatus` rows seeded by data migration, each with an admin-editable
  `CustomerMessage`: `processing` ("طلبك قيد المعالجة، أعد المحاولة بعد دقيقة") shown when the
  deadline passes, and `busy` ("الخدمة مشغولة حالياً، حاول بعد قليل") shown when enqueueing fails.

## Flow 1 — Refresh (public link, employee, SMS)

1. `POST /refresh/api/` validates phone, Origin, honeypot, token (as today) and the **Redis-backed**
   rate limit, then the cooldown check (unchanged logic). If any check short-circuits, it responds
   synchronously exactly as today.
2. Otherwise `providers.service.submit_refresh(phone, source, ip)` creates a `ProviderJob`, enqueues
   it on the `refresh` queue and the endpoint returns `202 {"job": "<uuid>", "status": "processing"}`
   in well under a second.
3. The page shows "جارٍ تحديث رقمك…" and polls `GET /refresh/status/<uuid>/` every 2 s.
4. Worker task `run_refresh(job_id)`:
   - resolves provider (forced provider / company field / default provider, as today);
   - checks provider enabled;
   - calls `adapter.refresh(phone)`;
   - on `AuthRequired`: `sessions.login(provider_key)` under a Redis lock
     (`lock:provider-login:<key>`, timeout 120 s). Jobs arriving during login block on the same lock
     and, after it is released, reuse the fresh session instead of logging in again;
   - on `TransientError`: retry with backoff, max 3 attempts, within the job deadline;
   - writes `RefreshLog` exactly as `_record_and_return` does today and stores the final status code
     + customer message on the job.
5. The status endpoint returns `{"status": "processing"}` until done, then the same payload shape
   the public page uses today (`status`, `message{title,body}`, optional last-refresh fields).
   It never exposes provider internals; unknown/expired ids return a generic "not found" message.
6. Deadline: if the job is not done after `SystemSettings.refresh_wait_seconds` (default 90), the
   page shows the `processing` status message.
   The job keeps running and its result is still logged.
7. Keep-alive: periodic task every 5 min (configurable) calls `sessions.ensure_alive(key)` for
   providers that need login (Sky), so login during customer traffic becomes rare.
8. SMS gateway: `refresh_client` enqueues the job and the outbox reply is sent when the job
   finishes (task callback) instead of blocking on an HTTP call.
9. Employee/internal-test screens use the same job + polling (internal test still bypasses
   service-off and cooldown, as today).
10. Fallback: if Redis or the worker is unreachable when enqueuing, the endpoint returns the
    `busy` status message (never a technical error), and the management dashboard
    shows a queue-health warning. Setting `REFRESH_ASYNC_ENABLED=0` restores the current synchronous
    path for emergency rollback during rollout.

## Flow 2 — Charge & record sale

1. Sales entry shows a second button "شحن وتسجيل بيع" next to the normal save, only when the
   selected company's adapter has `Capability.CHARGE` and the provider account is enabled.
2. Submitting it validates the same form, then in one transaction:
   - `create_sale(...)` as today (deducts supplier cost, ledger row, audit), with
     `entry_mode=auto_charge`, `charge_status=processing`;
   - creates `ProviderJob(kind=charge, sale=…, idempotency_key=<form token>)`. The form token is
     generated when the form renders, so a double-click/resubmit hits the unique key and returns
     the existing sale instead of charging twice.
   - Enqueue happens `on_commit`.
3. The employee is redirected to their entries immediately; the sale row shows the
   "شحن تلقائي" badge and a live status chip (HTMX polling while `processing`).
4. Worker task `run_charge(job_id)` calls `adapter.charge(phone, ctx)`; the adapter looks up the
   line/plan on the provider by phone (Sky: subscriber search → costs → task create), returns
   `ChargeOutcome`. `AuthRequired` is handled with the same lock + single retry **only if the
   failure happened before the charge request was sent**; adapters raise `AuthRequired` only in
   that case.
5. Outcomes:
   - `success` → `charge_status=success`, store ref/plan/provider cost; flag
     `charge_cost_mismatch` for management if provider cost ≠ `cost_price_snapshot`.
   - `failed` (provider definitively refused) → `charge_status=failed`, then
     `cancel_sale(sale, user=<system user>)` which restores supplier balance once (existing guard
     against duplicate reversal) and keeps the trail in ledger/audit.
   - `unverified` (timeout / ambiguous / transport error after sending) → `charge_status=unverified`.
     No retry, no reversal.
6. Periodic task `verify_unverified_charges` (every 2 min) calls `adapter.check_charge(ref or phone
   + time window)` for `unverified` sales; resolves to success/failed as above. After 30 min still
   unresolved → stays `unverified` and appears in a management "needs verification" list.
7. Management actions on charged sales: "إعادة الفحص" (enqueue check now) and "تأكيد يدوي" (mark
   success, or mark failed which triggers the same reversal). Both audited.
8. UI markers: badge + status chip in employee "my entries", management sales list, sale detail;
   management sales list gets a filter by `entry_mode` and `charge_status`.

## Flow 3 — Reconcile

- Management "report matching" page calls `providers.service.reconcile(company, from, to, opts)`,
  which enqueues a job on the `reports` queue (Sky's report fetch can be slow) and the page polls.
- Each adapter runs its own logic (`adapters/sky/reconcile.py`, `adapters/layan/reconcile.py`,
  moved with behaviour unchanged; Layan keeps its Excel upload input) and returns a
  `ReconcileReport`. Existing templates are adapted to render the report's sections.
- Existing reconcile tests move with the code and must keep passing unchanged in assertions.

## Security (scope of this sub-project)

- Provider credentials encrypted at rest (Fernet, key in env), write-only in UI, never logged.
- Log sanitiser strips passwords, OTP codes, JWTs, session ids, cookies from job results,
  `RefreshLog.raw_body`, and exception messages before persistence.
- Rate limiting for public refresh + status endpoints moves to Redis (shared across workers).
- `/api/v1/` tokens: stored hashed (as `ApiToken` today), plus scopes (`refresh`, `charge`) and
  per-token rate limit. Charge endpoint requires `charge` scope and an `Idempotency-Key` header.
- Job ids are UUID4; status endpoint returns only customer-facing fields.
- Charge from the UI requires an authenticated employee/management user (existing role checks)
  and CSRF.
- Sky session tokens live only in Redis (no more `/tmp` file); Redis is internal-only on Coolify
  with a password.

## Deployment

- New deps: `celery[redis]`, `redis`, `cryptography`.
- New env: `REDIS_URL`, `PROVIDER_SECRETS_KEY`, `REFRESH_ASYNC_ENABLED` (default 1).
- Coolify: add Redis service; add `worker` service from the same image with command
  `celery -A config worker -B -Q refresh,charge,reports --concurrency ${CELERY_CONCURRENCY:-4}`.
  Gunicorn timeout stays 60 s (no request waits on providers anymore).
- `CACHES` switches to Redis when `REDIS_URL` is set (shared cache across workers).
- Docs: `DEPLOY_COOLIFY.md` updated with the new services and env vars.

## Testing

- Adapter unit tests with recorded provider responses (no network), moved/extended from existing
  `tests_sky_provider.py`, `tests_sky_sales_client.py`, `tests_aloha_provider.py`, reconcile tests.
- Session lock test: N concurrent jobs with an expired session → exactly one login call.
- Refresh flow tests with Celery eager mode: enqueue → status polling payloads; deadline message;
  enqueue failure → "service busy"; `REFRESH_ASYNC_ENABLED=0` → current synchronous behaviour.
- Charge flow tests: success; confirmed failure → sale cancelled and balance restored exactly once;
  unverified → no reversal, check resolves later; idempotency key prevents double charge;
  cost-mismatch flag.
- Full existing suite stays green (`python manage.py test`).

## Clean-up included in this sub-project

- Remove `sky_lab` references (`_sky_env_paths` reading `sky_lab/.env`) and commit the already
  deleted `sky_lab/` files.
- Delete empty `captcha_lab/` and `phone_refresh/providers/captcha/`.
- Delete `phone_refresh/providers/` after moving adapters; delete `companies/sky_reconcile.py` and
  `companies/layan_reconcile.py` after moving.
- Remove `COMPANY_TO_PROVIDER` name matching and the `/tmp` session file code.

## Rollout

1. Deploy with Redis + worker and `REFRESH_ASYNC_ENABLED=0`; verify worker health page.
2. Save Sky account in the new admin screen; confirm keep-alive login succeeds.
3. Enable `REFRESH_ASYNC_ENABLED=1`; watch refresh logs for a day.
4. Enable the charge button for Sky.

## Roadmap (other sub-projects, each with its own spec)

2. System reorganisation & dead-code removal (clear sections: number refresh, sales & accounts,
   inventory, reports; remove `install.py` duplication vs Coolify, root-level dumps/exports).
3. Full security audit & hardening (login brute-force protection, admin, headers/HSTS, secrets,
   backups/PII files, dependency audit).
4. Online store foundation (unified inventory, points of sale with locations, SIM/product pricing).
5. New provider integrations (Layan, Areen, Aloha charge + check) on top of the gateway.
