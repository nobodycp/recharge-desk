# Automated installer (Ubuntu 24)

Config-driven, non-interactive installation for **single-server** deployments:
PostgreSQL, **Gunicorn** on loopback, **Caddy** for TLS + static/media.

## One-line remote install (clone + install)

On **Ubuntu 24** as root (pipe with `sudo`), pass **git URL** and **domain**. Third argument is the DB password; **omit it** to auto-generate one under `/root/recharge-desk.generated-db-password.txt`.

```bash
curl -fsSL https://raw.githubusercontent.com/nobodycp/recharge-desk/main/scripts/remote-install.sh | sudo bash -s -- \
  'https://github.com/nobodycp/recharge-desk.git' \
  's.prosim.ps' \
  'YOUR_POSTGRES_APP_PASSWORD'
```

Same with environment variables instead of positional args: `RECHARGE_REPO`, `RECHARGE_DOMAIN`, `RECHARGE_DB_PASSWORD` (optional), plus `RECHARGE_INSTALL_DIR`, `RECHARGE_CONFIG` — see [`scripts/remote-install.sh`](../scripts/remote-install.sh) header.

The script installs `git`/`python3`, clones or updates the repo under `/opt/recharge-desk`, merges **domain + password + Django hosts** into the JSON config, then runs **`python3 install/install.py`** (not `install.sh`) so there is no “command not found” from shebang/permissions.

## Install from an existing clone (two steps)

1. **Once:** copy the template next to the repo and edit only what `_edit_these` says in `install/config.example.json`:

```bash
cd /opt/recharge-desk   # or your clone path
sudo cp install/config.example.json install.config.json
sudo nano install.config.json
sudo chmod 600 install.config.json
```

2. **Any time:** run the launcher (no `--config` needed if `install.config.json` sits in the repo root):

```bash
sudo bash install.sh
```

Or: `sudo bash install.sh --config /root/recharge.install-config.json`  
`install.config.json` is gitignored so secrets are not committed.

## Preconditions (G)

1. **Fresh Ubuntu 24.04** (Jammy is not supported by this script’s OS check).
2. **DNS**: an `A` (or `AAAA`) record for your hostname (e.g. `s.prosim.ps`) must resolve to this server’s **public** address *before* Caddy can obtain a Let’s Encrypt certificate. Mail DNS is optional.
3. **Firewall**: allow inbound **80** and **443** for ACME and HTTPS.
4. **Root**: run as `root` or `sudo` (same requirement for `apt`, `systemd`, `postgresql`).
5. **Project source**:
   - **bundled**: run the installer **from the repository clone** on the server (it copies the tree that contains `install/install.py`).
   - **git**: set `project.git_url` / `project.git_ref` (branch or tag; not arbitrary SHAs unless shallow clone supports it).
   - **local**: set `project.local_path` to an existing directory tree that contains `manage.py`.

## Architecture (A)

- **Python 3 driver** (`install.py`, stdlib only): JSON config, validation, subprocess orchestration, safe templating.
- **`../install.sh`** (repository root): run with **`sudo bash install.sh`** from the clone root (defaults to `install.config.json` if present).
- **`bootstrap.sh`**: same as above but lives under `install/` (optional).

Reasoning: JSON is stdlib; no `PyYAML` dependency on a minimal server. `sudo`/`apt`/`systemctl` stay explicit shell commands from Python subprocess.

## Files (B)

| Path | Role |
|------|------|
| `install.sh` | **One-file launcher** at repo root → `install/install.py` |
| `install/install.py` | Main installer |
| `install/bootstrap.sh` | Alternate wrapper inside `install/` |
| `install/config.example.json` | Schema template (committed, no secrets) |
| `install/README.md` | This document |
| `scripts/remote-install.sh` | Curl-friendly clone + config + `install.sh` |

Generated on the server (not in git): private JSON config, `/etc/recharge-desk.env`, systemd unit, Caddy fragment under `/etc/caddy/sites/`.

## Config schema (C)

| Field | Type | Meaning |
|--------|------|--------|
| `domain` | string | Public hostname (Caddy site name + ACME), e.g. `s.prosim.ps`. |
| `system_user` | string | Linux user for file ownership and Gunicorn (`User=`). |
| `service_name` | string | systemd unit basename (`recharge-desk` → `recharge-desk.service`). |
| `force` | bool | If `true`, **delete** `paths.base` before install (destructive). |
| `dry_run` | bool | Plan only; skips mutating steps (also accepts CLI `--dry-run`). |
| `idempotent` | bool | If `true`, a non-empty `paths.base` is allowed when `paths.app/manage.py` already exists: skip re-copying sources, reuse venv with `pip install -r` only, and re-apply migrations / services (safe **upgrade / re-run**). |
| `strict_port_check` | bool | If `true`, abort when TCP **80** or **443** are already bound by a process that does not look like **Caddy** (helps catch Apache/nginx conflicts before reload). |
| `dns_check` | bool | If `true`, resolve `domain` and compare to detected public IPv4 (warning only). |
| `paths.base` | string | Install root (e.g. `/opt/recharge-desk`). |
| `paths.venv` | string | Python venv directory. |
| `paths.app` | string | Django project root (`manage.py` lives here). |
| `paths.static` | string | `DJANGO_STATIC_ROOT` / Caddy `file_server` root for `/static/`. |
| `paths.media` | string | `DJANGO_MEDIA_ROOT` / Caddy root for `/media/`. |
| `paths.env_file` | string | Absolute path for generated env file (e.g. `/etc/recharge-desk.env`). |
| `project.mode` | string | `bundled` \| `git` \| `local`. |
| `project.git_url` | string | Required if `mode=git`. |
| `project.git_ref` | string | Branch or tag (default `main`). |
| `project.local_path` | string | Required if `mode=local`. |
| `postgres.create_database` | bool | Create role+DB when `true`. |
| `postgres.host` / `port` | string/int | PostgreSQL connection. |
| `postgres.name` / `user` | string | Lowercase identifiers `[a-z_][a-z0-9_]{0,62}` (installer validates). |
| `postgres.password` | string | App DB password (keep only in private config). |
| `django.secret_key` | string\|null | If null/empty, a secure key is generated. |
| `django.allowed_hosts` | string[] | Host header values (no `*`). |
| `django.csrf_trusted_origins` | string[] | Must be `https://…` entries. |
| `django.log_level` | string | Optional, default `INFO`. |
| `gunicorn.bind` | string | **Must** be `127.0.0.1:PORT` or `unix:…` (never `0.0.0.0`). |
| `gunicorn.workers` / `timeout` | int | Gunicorn tuning. |
| `caddy.sites_dir` | string | Directory for site fragments, default `/etc/caddy/sites`. |
| `caddy.fragment_name` | string | Fragment filename (e.g. `recharge-desk.caddy`). |
| `caddy.ensure_sites_import` | bool | If `true`, prepend `import /etc/caddy/sites/*.caddy` to `/etc/caddy/Caddyfile` when missing (backup `.bak.installer`). |

Copy `config.example.json` to a private path (e.g. `/root/recharge.install-config.json`), set secrets, `chmod 600`, and **never commit** that file (see repo `.gitignore` for `*.install-config.json`).

## Safety (D)

- Refuses non-Ubuntu-24.04 / non-root.
- Refuses `gunicorn.bind` that is not loopback or unix socket.
- Refuses empty or non-HTTPS CSRF origins in config validation.
- Refuses `django.allowed_hosts` containing `*`.
- Refuses overwriting a non-empty `paths.base` unless `force: true`, **`idempotent: true`** with an existing `paths.app/manage.py`, **or** a detected **fresh bundled git checkout** at `paths.base` (clone root + empty `paths.app`) so `git clone` into `/opt/recharge-desk` works without `force`.
- **Path sanity:** `paths.*` must be absolute; `paths.base` / `app` / `venv` must not target `/`, `/etc`, `/bin`, etc.; `paths.env_file` parent must be writable.
- **PostgreSQL:** logs whether the role already exists; if the database exists, verifies **owner** matches `postgres.user` or aborts with a clear message (no silent takeover).
- **Ports 80/443:** warns (or aborts with `strict_port_check: true`) if another process already listens.
- **Health:** retries HTTPS and HTTP checks after services start (ACME / upstream warm-up).
- **Failures:** on any uncaught error, prints a **recovery** block (logs to inspect, `idempotent` re-run hint, Caddyfile backup path); **does not** drop databases automatically.
- PostgreSQL role/database identifiers restricted to safe `[a-z_][a-z0-9_]*`.
- Password embedded in PL/pgSQL using dollar-quoting.
- `DJANGO_SECRET_KEY` generated if omitted.
- Env file written with `0600`, then `chown root:system_user` + `chmod 640` so Gunicorn’s user can read `EnvironmentFile` without world-readable secrets.
- `manage.py migrate` / `collectstatic` / verification run as **application user** via `sudo -u … env …`.
- **chown:** verifies `paths.base` is a directory and `system_user` exists before `chown -R`.
- Caddyfile import line: backup before prepend; skip if import already present.

## Usage (F)

On the server (after `git clone`):

```bash
cd /path/to/recharge-desk
sudo cp install/config.example.json install.config.json
sudo nano install.config.json
sudo chmod 600 install.config.json
sudo bash install.sh
```

Use an explicit config path instead of `install.config.json` when you prefer:

```bash
sudo bash install.sh --config /root/recharge.install-config.json
```

Dry run:

```bash
sudo bash install.sh --config /root/recharge.install-config.json --dry-run
```

Equivalent (direct Python):

```bash
sudo python3 install/install.py --config /root/recharge.install-config.json
```

Re-run with `"force": true` only when replacing an existing deployment under `paths.base`.

## Post-install

- Create a superuser (not automated):  
  `sudo -u YOUR_APP_USER -H env $(grep -v '^#' /etc/recharge-desk.env | xargs) /opt/.../venv/bin/python /opt/.../app/manage.py createsuperuser`  
  (or a small wrapper that loads the env file safely).
- Watch logs: `journalctl -u recharge-desk.service -f` and Caddy logs via `journalctl -u caddy -f`.
