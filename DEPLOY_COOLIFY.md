# Deploying to Coolify — دليل النشر

This Django project ships with a production-grade `Dockerfile`, an
`entrypoint.sh` that either runs migrations + `collectstatic` and boots
gunicorn (`web`, the default) or starts the Celery worker (`worker`), and
WhiteNoise for static-file serving. Since the provider gateway (branch `v2`)
the stack is **four Coolify resources** built from the same image:

| Resource | What it is | Command |
|---|---|---|
| `db` | PostgreSQL (as before) | — |
| `redis` | Redis (broker, sessions/locks, rate limits, cache) | — |
| `web` | the application (gunicorn) | `web` (default) |
| `worker` | Celery worker + embedded beat | `worker` — **exactly one replica** |

Coolify needs to: build the Dockerfile, inject environment variables, and
route traffic from the configured domains to port `8000` of `web`.

---

## 1. Prerequisites — المتطلبات

- A running Coolify instance (v4+) with a publicly reachable IP.
  سيرفر Coolify شغّال وله IP عام.
- DNS `A`/`AAAA` records pointing at the Coolify server for every
  hostname you plan to use. Recommended set:
  - `prosim.ps` (apex / marketing — optional)
  - `s.prosim.ps` (admin / management panel — **required**)
  - `rn.prosim.ps` (public phone-refresh subdomain — **required**)

  وجّه الـ DNS لهذه النطاقات كلها على عنوان السيرفر قبل النشر.

---

## 2. Create the Postgres database — إنشاء قاعدة البيانات

1. In the Coolify UI: **New Resource → Database → PostgreSQL**.
2. Pick a name (e.g. `recharge-db`), accept defaults (Postgres 16 is fine),
   and create.
3. After it boots, open the database page and copy the **internal**
   `postgres://...` connection string. That is your `DATABASE_URL`.
   انسخ رابط الاتصال الداخلي — هو نفسه قيمة `DATABASE_URL`.

> Use the **internal** URL (it resolves over Coolify's docker network)
> unless your app runs in a different project; only then use the public URL.

**Critical — persistent database:** Link the application to this Postgres
resource and set `DATABASE_URL` to its **internal** connection string.
Do **not** point production at SQLite (e.g. `sqlite:///app/db.sqlite3`) —
the app container filesystem is recreated on every deploy, so any SQLite
file is wiped and `migrate` re-seeds default settings from scratch.

In Coolify: open the Postgres service → confirm **Persistent Storage** is
enabled (a named volume on `/var/lib/postgresql/data`). If the database
is recreated on each deploy, all admin settings (API limits, token
requirement, subdomain) will reset even though the code is correct.

---

## 2b. Create Redis — إنشاء Redis

Redis is **required** in production: `config.settings.production` refuses to
boot without `REDIS_URL` (it would otherwise silently fall back to in-process
fakeredis and eager Celery, i.e. no worker and no shared login locks).

1. **New Resource → Database → Redis** (same project/environment as Postgres).
2. Keep it **internal only** (do not publish a public port) and keep the
   generated password.
3. Copy the **internal** URL, e.g. `redis://default:PASSWORD@redis-xyz:6379/0`.
   That is your `REDIS_URL`. If the URL has no `/0` suffix, append it.
4. Persistence is optional: Redis only holds the Sky session, locks, queues and
   cache — all rebuildable. Jobs lost from the queue are re-created by users
   retrying; stuck jobs are reaped by a periodic task.

---

## 3. Create the application — إنشاء التطبيق

1. **New Resource → Application → Public Repository** (or **Private** if your
   GitHub repo is private — Coolify will walk you through the GitHub App
   install).
2. Repository: `https://github.com/nobodycp/recharge-desk.git` (or whichever
   remote you push to).
3. Branch: `main` (or whatever branch you push to).
4. Build pack: **Dockerfile** (Coolify will autodetect the `Dockerfile` at
   the repo root).
5. Port: **8000**.
6. Health check: **enabled**, type **Command** (`cmd`), command
   `python /app/scripts/healthcheck.py`; start period `30`, interval `10`,
   timeout `5`, retries `15`. The image has no curl/wget and Django rejects
   `Host: localhost`, so the default HTTP check cannot pass; the script calls
   `/healthz/` with an allowed host. With the check on, a deploy keeps the old
   container serving until the new one has migrated and booted (zero
   downtime), and a broken release is never switched in.
   فعّل فحص الصحة بنوع Command وإلا كل نشر بيوقف الموقع لحد ما يجهز الكونتينر الجديد.

---

## 4. Environment variables — متغيّرات البيئة

Open the application → **Environment Variables** → paste in the keys
below. Replace placeholder values with your real ones. كل المتغيرات أدناه
موجودة موثّقة في `.env.example` بنفس الترتيب.

```env
# Django
DJANGO_SETTINGS_MODULE=config.settings.production
DJANGO_SECRET_KEY=<run: python -c "import secrets; print(secrets.token_urlsafe(64))">
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=prosim.ps,s.prosim.ps,rn.prosim.ps
DJANGO_CSRF_TRUSTED_ORIGINS=https://prosim.ps,https://s.prosim.ps,https://rn.prosim.ps

# Database (paste the Postgres internal URL from step 2)
DATABASE_URL=postgres://USER:PASS@HOST:5432/DBNAME

# Redis (paste the Redis internal URL from step 2b) — REQUIRED
REDIS_URL=redis://default:PASSWORD@HOST:6379/0

# Fernet key that encrypts provider accounts in the DB — REQUIRED.
# Generate ONCE, keep a copy in your password manager. Losing/changing it makes
# the stored provider passwords unreadable (re-enter them in the admin screen).
PROVIDER_SECRETS_KEY=<run: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">

# FIRST ROLLOUT: keep 0 (old synchronous refresh path) until the worker and the
# Sky account are verified; flip to 1 in rollout step 3 below. (Default when
# unset is 1.)
REFRESH_ASYNC_ENABLED=0

# Coolify terminates TLS; do not let gunicorn issue another 301.
DJANGO_SECURE_SSL_REDIRECT=0
SECURE_HSTS_SECONDS=3600
SECURE_HSTS_INCLUDE_SUBDOMAINS=1
```

Optional knobs (defaults are fine for most deployments):

```env
GUNICORN_WORKERS=3
GUNICORN_THREADS=2
GUNICORN_TIMEOUT=60
DJANGO_LOG_LEVEL=INFO

# worker service only
CELERY_CONCURRENCY=4
```

The `web` and `worker` services need the **same** environment (same
`DATABASE_URL`, `REDIS_URL`, `PROVIDER_SECRETS_KEY`, `DJANGO_SECRET_KEY`, …).
In Coolify, use shared variables or copy the list to both applications.

Sky credentials are no longer env-driven. If `SKY_SALES_USER`,
`SKY_SALES_PASSWORD`, `SKY_SALES_TOTP_SECRET` (and optionally
`SKY_SALES_PROXY`) are still set on the **first** deploy, a data migration
imports them once into the encrypted provider account; afterwards manage the
account in **Phone Refresh → Providers → Sky** (the env vars remain only a
fallback until the account is saved there, then they are ignored and can be
removed):

```env
# one-time import only
SKY_SALES_USER=your_username
SKY_SALES_PASSWORD=your_password
SKY_SALES_TOTP_SECRET=XXXXXXXXXXXXXXXX
SKY_SALES_PROXY=http://LOGIN_s_skysales:PASSWORD@res.geonix.com:10000
```

Use a **sticky session** in the Geonix username (e.g. `LOGIN_s_skysales`) so
login and refresh share the same exit IP. Optional geo: `LOGIN_c_IL_s_skysales`.

Put the Geonix residential proxy URL in the Sky account's proxy field (or in
`SKY_SALES_PROXY` for the one-time import). Use **Runtime only** in Coolify
(never bake secrets into the Docker image). The Sky session lives in Redis
(`REDIS_URL`) — one login at a time, shared by all workers; a Celery beat job
keeps it warm.

> **Sky provider**: HTTP login + TOTP to `sales-ps.sky5g.ps:8888` — no browser
> required. Residential proxy required — datacenter IPs are rejected by Sky.

> **Important**: `DJANGO_ALLOWED_HOSTS` must list every hostname Coolify
> will route to this container. Forget one and Django answers `400 Bad
> Request` for that host.
>
> ملاحظة: لازم تكتب كل النطاقات اللي راح يجي عليها الطلب — أي نطاق
> مش موجود راح يرجّع `400`.

---

## 5. Domains — ربط النطاقات

In the application → **Domains** tab add the two public hostnames:

- `https://s.prosim.ps`  → admin / management panel.
- `https://rn.prosim.ps` → public phone-refresh page.

Coolify will request Let's Encrypt certificates automatically. اتركها
تستخرج الشهادات وحدها — أول طلب قد يأخذ دقيقة.

You can add `https://prosim.ps` too if the apex should also serve the
panel; just make sure it appears in `DJANGO_ALLOWED_HOSTS` /
`DJANGO_CSRF_TRUSTED_ORIGINS`.

---

## 5b. Create the worker — إنشاء الـ Worker

The worker runs refresh jobs, charge jobs, session keep-alive and the other
periodic tasks. Without it the public refresh page will answer "processing".

1. **New Resource → Application** from the **same repository, branch and
   Dockerfile** as `web` (name it e.g. `recharge-worker`).
2. Set the **Start Command** (a.k.a. custom command) to `worker`. The image's
   `ENTRYPOINT` is `./entrypoint.sh`, so this runs
   `celery -A config worker -B -Q refresh,charge,reports --concurrency ${CELERY_CONCURRENCY:-4}`.
   (If your Coolify version has no start-command field for Dockerfile apps,
   use *Custom Docker Options* → `--entrypoint "./entrypoint.sh worker"`.)
3. Copy **all** environment variables from `web` (see step 4).
4. **No domain, no port, no health-check path** — it serves no HTTP. Disable
   the HTTP health check for this resource.
5. **Replicas: exactly ONE.** The worker runs Celery beat embedded (`-B`);
   every extra replica would run its own beat and duplicate the periodic
   tasks (keep-alive logins, stuck-job reaping, charge verification). Scale
   throughput with `CELERY_CONCURRENCY`, never with replicas.
6. The worker does **not** run migrations or `collectstatic`; `web` does.
   Deploy `web` first on a release that adds migrations.

---

## 6. Deploy — النشر

Hit **Deploy**. Watch the **Build Logs** then **Runtime Logs** tabs. On a
healthy boot you will see, in order:

```
[entrypoint] Running migrations...
Operations to perform: ...
[entrypoint] Collecting static files...
[entrypoint] Starting gunicorn on 0.0.0.0:8000
[INFO] Booting worker with pid: ...
```

and for the `worker` service:

```
[entrypoint] Starting celery worker (queues: refresh,charge,reports)
... celery@... ready.
```

Subsequent deploys redo migrations idempotently — صفر downtime ضروري
هنا، لكن Coolify يدير الـ rolling restart تلقائياً.

---

### Rollout — خطوات الإطلاق

Roll the provider gateway out in this order (each step is reversible):

1. **Deploy** Redis + `web` + `worker` with `REFRESH_ASYNC_ENABLED=0`. Verify
   the worker is healthy: **Phone Refresh → Providers → General** shows Redis
   and worker status green.
2. **Save the Sky account** in **Phone Refresh → Providers → Sky** (or confirm
   the one-time env import landed); use the test button and confirm the
   keep-alive login succeeds (shows "last login OK").
3. **Enable async**: set `REFRESH_ASYNC_ENABLED=1` and redeploy `web`. Watch
   the refresh logs for a day. Emergency rollback = set it back to `0`.
4. **Charge button**: enable "Charge & record sale" for Sky once the Sky charge
   capability ships (not available yet, see below).

> **Keep step 1 short.** With `REFRESH_ASYNC_ENABLED=0` (and always for the
> Sky reconcile screen) a Sky login runs *inside the web request*. A cold login
> can take up to ~120 s, longer than gunicorn's 60 s timeout, so the first
> customer after a logout may get a killed request. Log in once from the admin
> provider test (step 2) before letting traffic in, and move to step 3 quickly.

### What changed for operators — ما الذي تغيّر

- **Public refresh is asynchronous**: the page gets a job id immediately and
  polls the status endpoint every 2 s; after `refresh_wait_seconds` (default
  90) it shows the "processing" message while the job keeps running and still
  logs its result. If Redis/worker are down customers see the "busy" message,
  never a technical error. `REFRESH_ASYNC_ENABLED=0` restores the old
  synchronous path. The management dashboard shows a warning when refresh
  jobs have been queued for over 2 minutes (worker down).
- **Login trouble shows "busy"**: while Sky is logged out the worker waits for
  the login (up to its 150 s budget); a failed login, the login cool-down
  (60 s, doubling per consecutive failure up to 15 min), or a disabled
  provider account answer customers with "busy". The real code is kept in the
  refresh log.
- **Provider accounts are encrypted in the DB** (Fernet, `PROVIDER_SECRETS_KEY`)
  and write-only in the UI. Sky `SKY_SALES_*` env vars are imported once by a
  migration; afterwards edit the account in the admin screen.
- **Sky session lives in Redis** with a single distributed login — concurrent
  requests no longer start parallel logins; the `/tmp` session file is gone.
- **Admin provider test** shows a scrubbed excerpt (max 500 chars) of the
  provider response; passwords, OTPs, tokens and cookies are stripped.
- **"Charge & record sale"** appears in sales entry only for providers that
  declare the CHARGE capability. **None today**: Sky charge/check-charge are
  blocked until a capture of the Sky Sales charge flow is provided, so the
  button stays hidden.
- **`/api/v1/` refresh API with scoped tokens**: `POST /api/v1/refresh/` and
  `GET /api/v1/refresh/<job>/` with `Authorization: Bearer <token>`; API
  tokens now carry scopes (create them in the **API** tab).

---

## 7. First-time superuser — إنشاء أول مستخدم

Coolify ships a per-container shell. Open the application → **Terminal**
(or **Commands → Open shell**) and run:

```bash
python manage.py createsuperuser
```

Enter username / email / password. Then visit
`https://s.prosim.ps/management/` (or your equivalent admin URL) and log in.
بعدها ادخل على لوحة الإدارة على نطاق `s.prosim.ps` وسجّل دخول.

---

## 8. Configure the public subdomain inside the app

After login, go to **Site Management** tab (إدارة الموقع) inside the
phone-refresh settings and set:

- `public_subdomain` → `rn.prosim.ps`
- `redirect_main_to_subdomain` → ON (recommended)

This activates `PhoneRefreshSubdomainMiddleware`, which:

- Serves only the public refresh page on `rn.prosim.ps`.
- Keeps the admin panel reachable on `s.prosim.ps`.
- Always allows `/healthz/` on every host (so Coolify health checks
  never go red).

---

## 9. Smoke checks — تحقق سريع بعد النشر

Tick all of these before announcing the deploy is done:

- [ ] `curl -fsS https://s.prosim.ps/healthz/` → `ok`.
- [ ] `curl -fsS https://rn.prosim.ps/healthz/` → `ok`.
- [ ] `https://s.prosim.ps/` → login screen renders with CSS (proves
      WhiteNoise is serving static assets).
- [ ] Login as the superuser; navigate to **Phone Refresh → Reports**.
- [ ] **Phone Refresh → Providers → General**: Redis ✔ and worker ✔.
- [ ] Worker runtime logs show `celery@... ready.` and no repeated
      `Starting celery worker` restarts (and the service has 1 replica).
- [ ] `https://rn.prosim.ps/` → public refresh form renders.
- [ ] Submit one phone number on the public page → upstream provider call
      succeeds, log row appears in **Reports**.
- [ ] **API** tab: create a token (scope `refresh`), then
      `curl -X POST https://s.prosim.ps/api/v1/refresh/ -H "Authorization: Bearer <token>" ...`
      returns a job id and `GET /api/v1/refresh/<job>/` resolves it.

---

## Updating / redeploying — تحديث الإصدار

1. Push to the connected branch.
2. Coolify auto-deploys (or click **Redeploy**).
3. Migrations run automatically when the `web` container starts
   (`entrypoint.sh web`). Redeploy the `worker` service too so it runs the same
   code (keep it at one replica).

No extra steps unless you added a new env var — في حال أضفت متغير جديد،
أضفه في تبويب **Environment Variables** قبل الـ redeploy.

---

## Troubleshooting — أخطاء شائعة

| Symptom | Cause | Fix |
|---|---|---|
| `DisallowedHost at /` | Hostname missing from `DJANGO_ALLOWED_HOSTS` | Add it, redeploy. |
| `CSRF verification failed` | Origin not in `DJANGO_CSRF_TRUSTED_ORIGINS` (and must be `https://`) | Add the `https://` origin, redeploy. |
| 502 from Coolify proxy | Container crashed during boot | Open **Runtime Logs**: usually a missing env var or DB unreachable. |
| Static files 404 / unstyled UI | `collectstatic` did not run | Check entrypoint logs; WhiteNoise needs `staticfiles/` populated. |
| Settings reset after every redeploy | `DATABASE_URL` uses SQLite inside the container, or Postgres has no persistent volume | Point `DATABASE_URL` at the Coolify Postgres internal URL; enable persistent storage on the DB service. Runtime logs show `Database engine=...postgresql...` on boot. |
| Sky refresh fails with login/proxy error | Missing credentials, bad TOTP, or proxy rejected | Re-save the Sky account in **Phone Refresh → Providers → Sky** (username, password, TOTP secret, proxy) and use the test button; check the worker Runtime Logs for `proxy_error` / `login_error`. |
| `web`/`worker` crash at boot: `...require the REDIS_URL environment variable` | `REDIS_URL` (or `PROVIDER_SECRETS_KEY`) missing | Set it on **both** services and redeploy. |
| Public page stays on "processing" / shows "busy" | Worker not running, wrong `REDIS_URL`, or worker on a different env | Check the worker Runtime Logs and **Providers → General** health; temporary fix: `REFRESH_ASYNC_ENABLED=0`. |
| Duplicate keep-alive logins / periodic tasks firing twice | `worker` has more than one replica (each runs embedded beat) | Scale `worker` back to exactly 1 replica. |
| Provider login fails right after a deploy with stored accounts | `PROVIDER_SECRETS_KEY` changed, so stored passwords cannot be decrypted | Restore the original key, or re-enter the provider accounts. |
