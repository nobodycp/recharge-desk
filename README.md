# Recharge Desk (MVP)

Internal web system for managing prepaid supplier balances, recharge products, employee sales entry, and financial reporting. Built with **Django 4.2**, **Bootstrap 5**, and **HTMX**. Local development uses **SQLite**; production targets **PostgreSQL** (see settings package below).

## Features (MVP)

- **Roles**: Management (full access) vs **Employee** (sales entry + own recent activity only; no profits, balances, reports, or settings).
- **Supplier companies** with opening balance, live **current balance**, and a full **balance ledger** (`CompanyBalanceTransaction`) for every movement (opening deposit, sale deduction, manual deposit, adjustment, cancellation reversal).
- **Products** with cost and default sell price; **sales** store `cost_price_snapshot` and `profit_snapshot` for historical accuracy.
- **Sales workflow**: employee creates **pending** sale → cost deducted from supplier balance immediately → management can **mark paid** (status only) or **cancel** (restores supplier cost once, guarded against duplicate reversals).
- **Management**: dashboard, companies/products/payment methods/users, sales list & filters, pending payments, profit/sales/expense/company reports, expenses.
- **i18n**: English + Arabic (`ar`). RTL uses Bootstrap RTL CSS when Arabic is active. Translation strings live in `locale/ar/LC_MESSAGES/django.po` (fill `msgstr` values, then compile).
- **Theme**: Light / dark toggle in the header (saved in the browser as `localStorage` key `recharge-desk-theme`). First visit follows the system preference (`prefers-color-scheme`).

## Requirements

- Python **3.9+** (tested on 3.9; Django 5.x was not available on this environment, so the project pins **Django 4.2**).

## Setup

```bash
cd /path/to/account_manger
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

Open `http://127.0.0.1:8000/`.

### تحديث سريع (`pull.sh`)

من جذر المشروع:

```bash
./pull.sh
```

ينفّذ `git pull --ff-only` فقط. لتمرير ريموت/فرع: `./pull.sh origin main`.

**تحديث السيرفر بعد النشر:** على الخادم (Ubuntu 24):

```bash
cd /opt/recharge-desk
sudo python3 install.py --app-update --git-pull
```

`--app-update` يعيد `pip` و`migrate` و`compilemessages` (يُنشئ `locale/**/LC_MESSAGES/*.mo` من ملفات `.po`) و`collectstatic` ثم إعادة تشغيل الخدمات باستخدام `deploy.json` داخل مجلد التثبيت. `--git-pull` اختياري لسحب آخر كود من `origin`. المثبّت يثبّت حزمة **`gettext`** عبر APT (لتوفير `msgfmt`). مع `--git-pull`، إذا تغيّر `install.py` على القرص يُعاد تشغيل المثبّت تلقائياً مرة واحدة حتى تُنفَّذ خطوات النسخة الجديدة (مثل `compilemessages`) في نفس الأمر. كما يُحدَّث **`DJANGO_ASSET_CACHE_BUSTER`** في ملف البيئة لكل تشغيل حتى تُحمَّل نسخ جديدة من ملفات **`/static/*.css`** و**`.js`** بدلاً من نسخة قديمة مخزّنة في المتصفح أو عند الوكيل (CDN).

**لوكال فيه ترجمة والسيرفر لا:** إذا كان التشغيل من `paths.app` داخل `paths.base`، `git pull` وحده يحدّث الجذر و**لا** يحدّث `app/`؛ المثبّت يزامن الجذر → `paths.app` بـ `rsync` في كل تشغيل (حتى لو شغّلت `install.py` من خارج المستودع). مع `project.mode: "git"` يُسحَب أيضاً استنساخ Git تحت `paths.app` عند `--git-pull`. نفّذ `install.py --app-update --git-pull` ثم تحقق من `ls -la <paths.app>/locale/ar/LC_MESSAGES/django.mo`. **لا حاجة لحذف التثبيت بالكامل** عادةً.

### Demo users (`seed_demo`)

| Username   | Password      | Role        |
|-----------|---------------|-------------|
| `admin`   | `admin1234`   | Management  |
| `employee1` | `employee1234` | Employee |

### Seeded reference data

- Companies: **Layan** (opening balance 5000), **Sky** (opening balance 0).
- Products: Layan — Weccom / Hot Mobile / Partner; Sky — Cellcom / Pelephone500 (costs and default prices as in your spec).
- Payment methods: Bank of Palestine, PalPay Wallet, Jawwal Pay Wallet.

## Translations

Arabic strings are pre-filled for the MVP UI. To refresh after template/code changes (excluding the virtualenv):

```bash
python manage.py makemessages -l ar --ignore=.venv
python tools/fill_ar_translations.py   # reapplies Arabic msgstr for known keys
python manage.py compilemessages
# أو يدوياً: msgfmt -c -o locale/ar/LC_MESSAGES/django.mo locale/ar/LC_MESSAGES/django.po
```

ملف **`locale/ar/LC_MESSAGES/django.mo`** مُتتبَّع في Git حتى يعمل العربي على السيرفر حتى لو تعذّر `compilemessages` هناك. بعد تعديل `django.po` شغّل `compilemessages` (أو `msgfmt`) محلياً ثم ارفع `django.po` و`django.mo` معاً. باقي كتالوجات `*.mo` تبقى مُستثناة؛ `compilemessages` في `install.py` ما زال يحدّثها عند توفر gettext.

Add new keys to `tools/fill_ar_translations.py` (`AR` dict) when you introduce new `gettext` / `{% trans %}` strings, then run the script again.

## Icon optimization (auto)

Every uploaded icon (`Company`, `ProductLine`, `Product`, `PaymentMethod`) is re-encoded by `core.image_utils.optimize_image` inside the model's `save()`: scaled to fit a **256×256** square, converted to **WebP @ q82**, EXIF stripped. New uploads need no action — typical savings are **90–95%** vs. the original PNG/JPEG.

Backfill files that were uploaded before this hook landed:

```bash
python manage.py optimize_icons --dry-run   # report only
python manage.py optimize_icons             # rewrite in place
```

Safe to re-run; already-optimized files are skipped.

## Example scenario (must work)

1. Log in as `employee1`.
2. Open **Sales entry** (`/employee/sales/`).
3. Choose **Sky** → **Cellcom**, number `0591234567`, selling price **70**, payment **Bank of Palestine**, payer **Mohammad**, save.
4. System stores pending sale, sets snapshots cost **45** / profit **25**, deducts **45** from Sky balance, writes a **deduction** ledger row linked to the sale.
5. Log in as `admin`, open **Pending payments**, mark as paid when money is received (no second supplier deduction).

## Project layout

- `accounts` — login/logout, user list/create/edit, `UserProfile` + role checks.
- `companies` — suppliers and products.
- `sales` — payment methods, sales, ledger, employee entry (HTMX product reload), management sales actions.
- `expenses` — business expenses + expense report.
- `reports` — dashboard, profit/sales/company reports (includes manual supplier top-ups & adjustments on the company report).
- `core` — home routing, forbidden page, `seed_demo` command, shared context (Bootstrap RTL/LTR).

## Settings modules

| Module | Use |
|--------|-----|
| `config.settings.development` | Default for `manage.py` / local `runserver` (SQLite, `DEBUG=True`). |
| `config.settings.production` | Default for `config.wsgi` / Gunicorn: PostgreSQL, env-based secrets, HTTPS headers behind a reverse proxy (e.g. Caddy). |

### Server deployment (Ubuntu 24, single host)

Everything is driven by **`install.py`** only (no separate install scripts, no checked-in config templates). On success it writes **`deploy.json`** under the install root (`chmod 600`) for later `--app-update` / `--uninstall`.

**One-shot install from GitHub** (clone + stack + TLS; replace `OWNER/REPO` and secrets):

```bash
curl -fsSL https://raw.githubusercontent.com/OWNER/REPO/main/install.py | \
  sudo python3 - --repo https://github.com/OWNER/REPO.git --domain app.example.com --db-pass 'STRONG_DB_PASSWORD'
```

Default install root: **`/opt/recharge-desk`**. Override with `--install-root /other/path`. Branch defaults to **`main`** (`--branch`).

**Install from an existing clone** (no `curl`; `git remote origin` must be set):

```bash
cd /path/to/clone
sudo python3 install.py --domain app.example.com --db-pass 'STRONG_DB_PASSWORD'
```

**Upgrade:** `sudo python3 /opt/recharge-desk/install.py --app-update` (optional `--git-pull`).

**Uninstall:** `sudo UNINSTALL_CONFIRM=YES python3 /opt/recharge-desk/install.py --uninstall --install-root /opt/recharge-desk`  
Optional: `UNINSTALL_DROP_POSTGRES=YES`, `UNINSTALL_REMOVE_SYSTEM_USER=YES`, `UNINSTALL_REMOVE_PROJECT_ROOT=YES`.

** Preconditions:** DNS for `domain` → this server; inbound **80**/**443**; run as **root**.

**Cloudflare:** If the public HTTPS probe fails while origin is fine, re-run with **`--skip-public-https-check`**.

**Local dev:** No `.env` file is required for the default SQLite setup; optional variables are documented in `config/settings/development.py` (`DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, …). Production uses **`/etc/recharge-desk.env`** (or the path implied by `--service-name`) generated by the installer — never commit that file.

### Test PostgreSQL locally

Development defaults to SQLite for zero-config setup. To exercise the production database engine on your laptop (recommended before any schema-changing release), export the same env vars used in production and `manage.py` will switch automatically:

```bash
export POSTGRES_DB=recharge_dev
export POSTGRES_USER=recharge
export POSTGRES_PASSWORD=devpass
export POSTGRES_HOST=127.0.0.1
export POSTGRES_PORT=5432
python manage.py migrate
python manage.py test
```

Unset any of those vars (or leave `POSTGRES_DB` empty) to fall back to SQLite. The test runner under `config.settings.production` always uses an in-memory SQLite, so CI never needs PostgreSQL credentials.

### Migrating an existing SQLite deployment to PostgreSQL

If a production server is still on SQLite, do this once to cut over without losing data:

```bash
# 1. From the running app on SQLite, export everything except auth permissions.
python manage.py dumpdata \
    --natural-foreign --natural-primary \
    -e contenttypes -e auth.Permission -e admin.LogEntry -e sessions \
    --indent 2 -o /tmp/recharge_dump.json

# 2. Provision PostgreSQL, set POSTGRES_* in /etc/recharge-desk.env,
#    re-deploy so DJANGO_SETTINGS_MODULE points at production.

# 3. On the new database, apply migrations on an empty schema, then load.
python manage.py migrate
python manage.py loaddata /tmp/recharge_dump.json

# 4. Smoke test: log in, view dashboard, run the orphan-ledger sanity check.
python manage.py cleanup_orphan_ledger --dry-run
```

Take a fresh `pg_dump` immediately after step 4 as the new baseline backup.

### Daily PostgreSQL backups

A turn-key script lives at `scripts/backup_postgres.sh`. It reads the
same `/etc/recharge-desk.env` Django reads (so credentials, host and
port stay in one place), runs `pg_dump -Fc`, names the file by date,
and rotates so disk usage stays bounded.

Install once:

```bash
sudo install -m 750 -o postgres -g postgres \
    /opt/recharge-desk/app/scripts/backup_postgres.sh \
    /opt/recharge-desk/scripts/backup_postgres.sh

sudo mkdir -p /var/backups/recharge-desk/db
sudo chown postgres:postgres /var/backups/recharge-desk/db
sudo chmod 750 /var/backups/recharge-desk/db

sudo -u postgres crontab -l 2>/dev/null > /tmp/pg.cron
echo '0 3 * * * /opt/recharge-desk/scripts/backup_postgres.sh' >> /tmp/pg.cron
sudo -u postgres crontab /tmp/pg.cron
rm /tmp/pg.cron
```

Tunables (override via the env file or the cron line):

| Variable        | Default                          | Purpose                                  |
|-----------------|----------------------------------|------------------------------------------|
| `BACKUP_ROOT`   | `/var/backups/recharge-desk/db`  | Where dumps land.                        |
| `KEEP_DAILY`    | `14`                             | How many recent daily files to keep.     |
| `KEEP_WEEKLY`   | `8`                              | How many Sunday files to keep on top.    |
| `BACKUP_REMOTE` | (unset)                          | Optional rsync target or `s3://bucket`.  |
| `LOG_FILE`      | `/var/log/recharge-desk-backup.log` | Per-run log; cron emails on failure. |

Sanity check (run as `postgres`):

```bash
sudo -u postgres /opt/recharge-desk/scripts/backup_postgres.sh
ls -lh /var/backups/recharge-desk/db
```

Verify the dump itself is restorable on a scratch database every
month or so — an untested backup is not a backup.

### Restoring a PostgreSQL backup

```bash
# 1. Pick a dump.
ls /var/backups/recharge-desk/db
DUMP=/var/backups/recharge-desk/db/recharge-desk-2026-04-19_03-00.dump

# 2. Stop the app so nothing writes mid-restore.
sudo systemctl stop recharge-desk

# 3. Drop & recreate the target database (DESTRUCTIVE).
#    Run as the postgres OS user; substitute the env values you use.
sudo -u postgres psql <<SQL
DROP DATABASE IF EXISTS rechargedesk;
CREATE DATABASE rechargedesk OWNER rechargedesk;
SQL

# 4. Restore. -j parallelises across cores, --clean is harmless on a fresh DB.
sudo -u postgres pg_restore \
    --dbname=rechargedesk \
    --no-owner \
    --no-privileges \
    --jobs=4 \
    "$DUMP"

# 5. Bring the app back, then sanity-check.
sudo systemctl start recharge-desk
curl -sf https://s.prosim.ps/management/ -o /dev/null && echo "OK"
sudo -u rechargedesk /opt/recharge-desk/venv/bin/python \
    /opt/recharge-desk/app/manage.py cleanup_orphan_ledger --dry-run
```

If the schema in the dump is older than the running code, run
`manage.py migrate` after step 4 — Django will apply missing migrations
without touching existing rows.

### Reverse proxy trust (production)

Production settings enable **`SECURE_PROXY_SSL_HEADER`** and **`USE_X_FORWARDED_HOST`** so Django treats requests as HTTPS and uses the public `Host` when **Caddy** (or another **trusted** reverse proxy) sets `X-Forwarded-Proto` and related headers. Those headers **must not** be accepted from arbitrary clients. Therefore the **Gunicorn process must not be exposed on a public interface**: bind it to **`127.0.0.1`** or a **Unix socket** and place **only** the reverse proxy on the public network (with firewall rules consistent with that design). If the app is reachable directly from the internet while trusting forwarded headers, clients could spoof headers and undermine HTTPS/host checks.

## Notes

- **Supplier balance** can go negative if opening balance is low and sales consume cost; this matches “internal statement vs supplier portal” workflows—use deposits/adjustments on the company report to align.
- **Production**: use `install.py` as above, or set `DJANGO_SETTINGS_MODULE=config.settings.production` with a real `EnvironmentFile`, PostgreSQL, `collectstatic`, Caddy for `/static/` and `/media/`, and Gunicorn on loopback or a Unix socket. Do not run `seed_demo` on a real production database unless you intend to load demo data.

## License

Proprietary / internal use.
