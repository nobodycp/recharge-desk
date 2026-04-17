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

`--app-update` يعيد `pip` و`migrate` و`collectstatic` وإعادة تشغيل الخدمات باستخدام `deploy.json` داخل مجلد التثبيت. `--git-pull` اختياري لسحب آخر كود من `origin`.

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

### Reverse proxy trust (production)

Production settings enable **`SECURE_PROXY_SSL_HEADER`** and **`USE_X_FORWARDED_HOST`** so Django treats requests as HTTPS and uses the public `Host` when **Caddy** (or another **trusted** reverse proxy) sets `X-Forwarded-Proto` and related headers. Those headers **must not** be accepted from arbitrary clients. Therefore the **Gunicorn process must not be exposed on a public interface**: bind it to **`127.0.0.1`** or a **Unix socket** and place **only** the reverse proxy on the public network (with firewall rules consistent with that design). If the app is reachable directly from the internet while trusting forwarded headers, clients could spoof headers and undermine HTTPS/host checks.

## Notes

- **Supplier balance** can go negative if opening balance is low and sales consume cost; this matches “internal statement vs supplier portal” workflows—use deposits/adjustments on the company report to align.
- **Production**: use `install.py` as above, or set `DJANGO_SETTINGS_MODULE=config.settings.production` with a real `EnvironmentFile`, PostgreSQL, `collectstatic`, Caddy for `/static/` and `/media/`, and Gunicorn on loopback or a Unix socket. Do not run `seed_demo` on a real production database unless you intend to load demo data.

## License

Proprietary / internal use.
