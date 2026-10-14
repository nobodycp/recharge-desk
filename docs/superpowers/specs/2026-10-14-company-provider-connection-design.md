# Company ↔ Provider Connection (ربط الشركة مع المزوّد) — Design

**Date:** 2026-10-14
**Status:** Design approved in chat; awaiting written-spec review
**Builds on:** `2026-10-14-provider-gateway-design.md` (deployed to production 2026-10-14)

## Problem

The provider gateway made the *backend* independent (`providers` app: encrypted
account, login, Redis session, adapters; refresh, charge and reconcile all call
`providers.service`). The *product surface* is still owned by phone refresh:

- The provider account cards and the "test" button live at
  `management/phone-refresh/providers/` next to refresh-only things (response
  rules, refresh on/off). The test button runs a **number refresh**, not a login.
- The company → provider link is `Company.phone_refresh_provider`, although
  charge (`sales/charging.py`) and reconcile (`company_supports`) read it too.
- The on/off switch is `phone_refresh.ProviderConfig` and only gates refresh;
  charge and reconcile have no switch.
- Redis / worker health is shown on a phone-refresh tab although every service
  depends on it.

The business model is: **the company is the root** (Sky, Aloha, Layan, Areen);
its connection to its provider system belongs to the company; refresh, charge
and reconcile are **services of that connection**. Refresh is one service among
others and must not own credentials or switches the others use.

## Goals

1. Manage each company's connection from the **company page**: system, account,
   login test, per-service switches and status.
2. Every service (refresh, charge, reconcile) is gated by its own switch on the
   connection, plus the connection's master switch.
3. The phone-refresh pages keep only refresh-specific configuration.
4. Gateway health (Redis, worker, provider logins) surfaces on the management
   dashboard as an alert, and as one line in the connection tab.
5. Zero-downtime deploy: the old container keeps working while the new one
   migrates (rolling update with health check).

## Non-goals

- Binding the account to a `Company` row (FK). Accounts stay keyed by
  `provider_key` (one company per provider system, one account per company — as
  agreed for the gateway). Revisit only if two companies ever share one system
  with different accounts.
- Sky charge capability (still blocked on the API capture).
- Any change to refresh / charge / reconcile *logic* inside adapters.

## Design

### 1. Data

**`providers.ProviderConnection`** — the existing `ProviderAccount` renamed
(`RenameModel`, `db_table` kept as `providers_provideraccount` so no table
rename happens). One row per provider key. Fields:

| Field | Notes |
|---|---|
| `provider_key` | unchanged, unique |
| `is_enabled` | unchanged: master switch; off = no login, no service |
| `username`, `secret_encrypted`, `totp_secret_encrypted`, `extra_encrypted` | unchanged; **optional** — adapters with `needs_login = False` (e.g. Aloha, Areen) never require them |
| `refresh_enabled` | new, default `True`; data-migrated from `phone_refresh.ProviderConfig.is_enabled` |
| `charge_enabled` | new, default `False` (nothing starts charging without an explicit switch) |
| `reconcile_enabled` | new, default `True` (current behaviour) |
| `last_login_at`, `last_login_ok`, `last_error`, `updated_at` | unchanged |

Rows are created on demand (`ProviderConnection.for_key(key)` = get-or-create)
so every registered adapter always has a connection, with or without
credentials.

A service is **active** for a company when all hold:
`company.provider_key` maps to a registered adapter **and** the adapter declares
the capability **and** `connection.is_enabled` **and** the service switch is on.
This lives in one function, `providers.service.service_state(company, capability)`,
returning `active | off | unsupported | not_configured` (+ reason text) — used by
the company tab, the sales charge button, the reconcile buttons and refresh
routing. `company_supports` is replaced by it.

**`Company.phone_refresh_provider` → `Company.provider_key`** (`RenameField`
with `db_column="phone_refresh_provider"`, so the column is not renamed).
Verbose name "نظام الشركة" / "Company system".

**`phone_refresh.ProviderConfig`**: its data is copied into `refresh_enabled`
by a providers data migration and no code reads it any more; the model and
table are dropped in the **next** release (see §5).

Accounts helpers keep their names where they describe credentials
(`get_credentials`, `record_login`, `ensure_enabled`); model references switch
to `ProviderConnection`.

### 2. Screens

**Company page — new tab "الربط مع المزوّد"** (`companies` statement tabs,
loaded with the same HTMX partial pattern as General / Sales / Ledger;
management only):

1. **Company system** — select (Sky / Aloha / Layan / Areen / not set), saved
   to `Company.provider_key`. The field also stays in the company edit form.
2. **Connection card** (only when a system is set):
   - master switch;
   - account form (same write-only behaviour as today: blank field keeps the
     stored value) — shown only when the adapter `needs_login`;
   - **"اختبار تسجيل الدخول"** button — tests the login only, never a number
     (see §3);
   - last login time / result / scrubbed last error.
3. **Services list** — one row per capability (تحديث الأرقام، الشحن، المطابقة):
   switch + state badge from `service_state`. Unsupported services render
   disabled with a reason (e.g. "الشحن غير متاح لسكاي بعد"). Reconcile row links
   to the existing reconcile screen when active.
4. **System line** — Redis ✔/✖, worker ✔/✖ (from `providers.health.status()`).

Saving any part posts to `companies` views
(`management/companies/<pk>/connection/...`), redirects back to the tab with a
flash message.

**Phone-refresh providers page** keeps: per-provider response rules, the
number test (admin diagnostics), reports. Removed: account cards, on/off
toggles, health card. The General tab shows a short note with links to each
company's connection tab.

**Old URLs**: the providers page (`management/phone-refresh/providers/`) keeps
working. The POST-only endpoints `.../providers/<key>/account/` and
`.../providers/general/save/` are removed (only the old forms posted to them;
nothing links to them, so there is nothing to redirect). The page's old
`?tab=<key>` links still open that provider's rules tab.

**Management dashboard** — the existing queue warning is extended into one
"حالة الربط" alert shown only when something is wrong: Redis down, worker
down / jobs stuck, or a connection whose `last_login_ok` is false while
enabled — each item links to the company's connection tab.

### 3. Behaviour

- **Refresh** routing checks `service_state(company, REFRESH)` instead of
  `ProviderConfig.is_provider_enabled`; when off the customer gets the same
  `provider_off` status/message as today. `bypass_provider_off` (admin number
  test) keeps bypassing it.
- **Charge**: `sales.charging.can_charge` uses `service_state(..., CHARGE)`;
  the "شحن وتسجيل بيع" button appears only when active.
- **Reconcile**: the company page reconcile buttons appear only when
  `service_state(..., RECONCILE)` is active.
- **Login test** (`POST .../connection/test-login/`): clears the provider's
  Redis session and cool-down, then enqueues
  `providers.tasks.test_connection(provider_key)` which runs
  `service.keepalive` (forces a fresh login through the normal single-login
  lock). The tab polls (HTMX, every 2 s, max 150 s) the connection's
  `last_login_at/ok/error` and shows the result. Never runs a login inside the
  web request (a cold Sky login can exceed gunicorn's timeout).
- **Saving credentials** clears the stored session and cool-down so the next
  call logs in with the new values.

### 4. Error handling

- Redis/worker down while pressing "test": the enqueue fails → immediate
  "الخدمة غير متاحة حالياً (Redis/worker)" message, nothing stored.
- Unreadable secrets (key changed): card shows the existing "أعد إدخال
  البيانات" warning; services show `not_configured`.
- Company with no system set: tab shows only the system selector.

### 5. Migration & deploy safety

- No table/column renames (`db_table`, `db_column` kept) → the old container,
  still running during the rolling update, keeps reading/writing the same
  columns.
- New boolean columns are added with `AddField(default=...)`; Django 4.2 does
  not keep a DB-level default, so an INSERT from the old container would fail.
  The only old-code writer of that table is the old account-card save; during
  the ~1 min overlap nobody uses it. Accepted risk; reads are unaffected.
- `ProviderConfig` is still read by the old container's refresh routing during
  the overlap, so its table drop is **deferred to the next release**.

### 6. Testing

- Migration test: `ProviderConfig.is_enabled=False` for a key → connection
  `refresh_enabled=False`.
- `service_state` matrix: unsupported / no system / master off / service off /
  active.
- Refresh returns `provider_off` when `refresh_enabled=False`; admin bypass
  still works.
- Charge button hidden when `charge_enabled=False`; reconcile buttons hidden
  when `reconcile_enabled=False`.
- Login test enqueues the task and never calls the adapter in-request; saving
  credentials clears the session.
- Company connection tab renders for management only; employee gets 403/redirect.
- Dashboard alert appears for Redis down / failed login and not when healthy.
- Full suite + JS harnesses green; `makemigrations --check` clean.
