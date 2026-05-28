# Deploying to Coolify — دليل النشر

This Django project ships with a production-grade `Dockerfile`, an
`entrypoint.sh` that runs migrations + `collectstatic` and then boots
gunicorn, and WhiteNoise for static-file serving. Coolify only needs to:

1. Build the Dockerfile.
2. Inject environment variables.
3. Route traffic from the configured domains to port `8000`.

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
6. Health check path: `/healthz/` (returns `ok`).
   مسار فحص الصحة هو `/healthz/`.

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

# Coolify terminates TLS; do not let gunicorn issue another 301.
DJANGO_SECURE_SSL_REDIRECT=0
SECURE_HSTS_SECONDS=3600
SECURE_HSTS_INCLUDE_SUBDOMAINS=1
```

Optional knobs (defaults are fine for most deployments):

```env
GUNICORN_WORKERS=3
GUNICORN_THREADS=2
GUNICORN_TIMEOUT=120
DJANGO_LOG_LEVEL=INFO

# Sky reCAPTCHA (Playwright Firefox — installed in Docker image)
SKY_CAPTCHA_BACKEND=firefox
SKY_PLAYWRIGHT_HEADLESS=1
SKY_BROWSER_WAIT_SEC=1
SKY_BROWSER_REUSE=1
SKY_BROWSER_MAX_USES=30
```

> **Sky provider**: the Docker image includes Playwright + Firefox (~100MB).
> Each Sky refresh launches a headless browser (~15–30s). Use
> `GUNICORN_TIMEOUT=120` or higher. Allocate at least **1GB RAM** per
> container if Sky traffic is steady.

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

Subsequent deploys redo migrations idempotently — صفر downtime ضروري
هنا، لكن Coolify يدير الـ rolling restart تلقائياً.

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
- [ ] `https://rn.prosim.ps/` → public refresh form renders.
- [ ] Submit one phone number on the public page → upstream provider call
      succeeds, log row appears in **Reports**.
- [ ] **API** tab: create a token, hit `/phone-refresh/api/refresh/`
      with `curl -H "Authorization: Token <token>"` and confirm `200`.

---

## Updating / redeploying — تحديث الإصدار

1. Push to the connected branch.
2. Coolify auto-deploys (or click **Redeploy**).
3. Migrations run automatically on container start via `entrypoint.sh`.

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
| Sky refresh timeout / worker killed | Browser captcha slower than 60s | Set `GUNICORN_TIMEOUT=120`; ensure container has ≥1GB RAM. |
| Sky `captcha: playwright is not installed` | Old image without Playwright | Redeploy with fresh Docker build (see Dockerfile). |
