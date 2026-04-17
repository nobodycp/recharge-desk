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

من جذر المشروع بعد إنشاء `.venv` مرة واحدة:

```bash
./pull.sh
```

يعمل `git pull --ff-only` ثم `pip install` و`migrate` داخل `.venv`. لتمرير فرع/ريموت صريح: `./pull.sh origin main`.

**تحديث السيرفر من نفس الماكينة** (بعد ضبط مفاتيح SSH):

```bash
export DEPLOY_SSH=root@YOUR_SERVER_IP
# اختياري إذا مسار الاستنساخ ليس الافتراضي:
# export DEPLOY_INSTALL_DIR=/opt/recharge-desk
./pull.sh
```

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
msgfmt -o locale/ar/LC_MESSAGES/django.mo locale/ar/LC_MESSAGES/django.po
```

Add new keys to `tools/fill_ar_translations.py` (`AR` dict) when you introduce new `gettext` / `{% trans %}` strings, then run the script again.

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

For unattended server installation, see **`install/README.md`**. **Fast path:** on Ubuntu 24, `curl …/scripts/remote-install.sh | sudo bash -s -- 'https://github.com/…/your.git' 'your.domain'` (clone + config + install in one go). **From a clone:** copy `install/config.example.json` → `install.config.json` in the repo root, edit `_edit_these` fields, `chmod 600`, then **`sudo bash install.sh`** (no `--config` needed). `install.config.json` is gitignored.

**Environment configuration:** **`.env.example`** in the repository is a **template only** (safe to commit). It documents variable names and example non-secret values. For any real deployment—including one created by a future automated installer—you must **create a separate `.env` file** (or use systemd `EnvironmentFile=` pointing at a path such as `/etc/recharge-desk.env`) by **copying** from `.env.example` and filling in real values. **Never put secrets in `.env.example`**; real secrets belong only in the deployed `.env` (or secret store) that is **not** committed to version control. An installer should generate or populate that private file, not edit the template in the repo.

Example **Caddy** and **systemd** snippets live under **`deploy/`** (not used by Django at runtime; for operators / installer output).

### Reverse proxy trust (production)

Production settings enable **`SECURE_PROXY_SSL_HEADER`** and **`USE_X_FORWARDED_HOST`** so Django treats requests as HTTPS and uses the public `Host` when **Caddy** (or another **trusted** reverse proxy) sets `X-Forwarded-Proto` and related headers. Those headers **must not** be accepted from arbitrary clients. Therefore the **Gunicorn process must not be exposed on a public interface**: bind it to **`127.0.0.1`** or a **Unix socket** and place **only** the reverse proxy on the public network (with firewall rules consistent with that design). If the app is reachable directly from the internet while trusting forwarded headers, clients could spoof headers and undermine HTTPS/host checks.

## Notes

- **Supplier balance** can go negative if opening balance is low and sales consume cost; this matches “internal statement vs supplier portal” workflows—use deposits/adjustments on the company report to align.
- **Production**: set `DJANGO_SETTINGS_MODULE=config.settings.production`, load configuration from a **real `.env`** (or equivalent `EnvironmentFile`) derived from `.env.example` as described above, run migrations against PostgreSQL, `collectstatic`, serve `/static/` and `/media/` from Caddy (or equivalent), and run Gunicorn bound to loopback or a Unix socket (see `deploy/`). Do not run `seed_demo` on a real production database unless you intend to load demo data.

## License

Proprietary / internal use.
