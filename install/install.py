#!/usr/bin/env python3
"""
Non-interactive, config-driven installer for Recharge Desk on Ubuntu 24.

Requires root (sudo). Does not modify Django business logic.
See install/README.md and install/config.example.json.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

INSTALL_DIR = Path(__file__).resolve().parent
REPO_ROOT = INSTALL_DIR.parent


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------

REQUIRED_TOP = ("domain", "system_user", "service_name", "paths", "project", "postgres", "django", "gunicorn", "caddy")


def _die(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def _warn(msg: str) -> None:
    print(f"WARNING: {msg}", file=sys.stderr)


def print_failure_recovery(
    *,
    phase: str,
    err: BaseException,
    cfg: dict[str, Any] | None = None,
    caddyfile_backup_hint: bool = False,
) -> None:
    """Best-effort human guidance after a failed install (no automatic destructive rollback)."""
    print("\n" + "=" * 72, file=sys.stderr)
    print(f"INSTALL FAILED during: {phase}", file=sys.stderr)
    print("=" * 72, file=sys.stderr)
    print(f"Exception: {type(err).__name__}: {err}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    print("\nRecovery (manual):", file=sys.stderr)
    print("  1) Inspect logs: journalctl -xeu postgresql", file=sys.stderr)
    if cfg:
        svc = str(cfg.get("service_name", "recharge-desk"))
        print(f"  2) App service: journalctl -u {svc}.service -n 200 --no-pager", file=sys.stderr)
        print("  3) Caddy: journalctl -u caddy -n 200 --no-pager", file=sys.stderr)
        print(
            f"  4) Fix the issue, then re-run with \"idempotent\": true to skip "
            f"re-copying the project tree (if paths already populated).",
            file=sys.stderr,
        )
    if caddyfile_backup_hint:
        print(
            "  5) If /etc/caddy/Caddyfile was modified, restore from "
            "/etc/caddy/Caddyfile.bak.installer if needed, then: systemctl reload caddy",
            file=sys.stderr,
        )
    print(
        "  6) This script does NOT drop PostgreSQL databases on failure "
        "(avoid accidental data loss). Clean up DB/roles manually if you must redo.",
        file=sys.stderr,
    )
    print("=" * 72 + "\n", file=sys.stderr)


def load_config(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        _die(f"Cannot read or parse config JSON ({path}): {e}")
    if not isinstance(data, dict):
        _die("Config root must be a JSON object.")
    return data


def validate_config(cfg: dict[str, Any]) -> None:
    for key in REQUIRED_TOP:
        if key not in cfg:
            _die(f"Missing required top-level key: {key}")

    paths = cfg["paths"]
    if not isinstance(paths, dict):
        _die("paths must be an object.")
    for pk in ("base", "venv", "app", "static", "media", "env_file"):
        if pk not in paths or not isinstance(paths[pk], str) or not paths[pk].strip():
            _die(f"paths.{pk} is required and must be a non-empty string.")

    proj = cfg["project"]
    if not isinstance(proj, dict):
        _die("project must be an object.")
    mode = proj.get("mode", "")
    if mode not in ("bundled", "git", "local"):
        _die('project.mode must be one of: "bundled", "git", "local".')
    if mode == "git":
        if not str(proj.get("git_url", "")).strip():
            _die("project.git_url is required when mode is git.")
    if mode == "local":
        lp = str(proj.get("local_path", "")).strip()
        if not lp:
            _die("project.local_path is required when mode is local.")
        if not Path(lp).is_dir():
            _die(f"project.local_path is not a directory: {lp}")

    pg = cfg["postgres"]
    if not isinstance(pg, dict):
        _die("postgres must be an object.")
    if "port" not in pg:
        _die("postgres.port is required")
    try:
        int(pg["port"])
    except (TypeError, ValueError):
        _die("postgres.port must be an integer")
    for pk in ("host", "name", "user", "password"):
        if pk not in pg or str(pg[pk]).strip() == "":
            _die(f"postgres.{pk} is required (non-empty).")
    if not isinstance(pg.get("create_database", True), bool):
        _die("postgres.create_database must be a boolean.")

    ident = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
    for fld in ("name", "user"):
        if not ident.match(str(pg[fld])):
            _die(
                f'postgres.{fld} must match ^[a-z_][a-z0-9_]{{0,62}}$ (lowercase '
                f"identifiers only, for safe automated SQL)."
            )

    dj = cfg["django"]
    if not isinstance(dj, dict):
        _die("django must be an object.")
    hosts = dj.get("allowed_hosts")
    origins = dj.get("csrf_trusted_origins")
    if not isinstance(hosts, list) or not hosts:
        _die("django.allowed_hosts must be a non-empty array of strings.")
    if not isinstance(origins, list) or not origins:
        _die("django.csrf_trusted_origins must be a non-empty array of strings.")
    for o in origins:
        if not isinstance(o, str) or not o.startswith("https://"):
            _die(f'Each django.csrf_trusted_origins entry must start with "https://": {o!r}')
    sk = dj.get("secret_key")
    if sk is not None and (not isinstance(sk, str) or not sk.strip()):
        _die("django.secret_key must be null or a non-empty string.")

    gun = cfg["gunicorn"]
    if not isinstance(gun, dict):
        _die("gunicorn must be an object.")
    bind = str(gun.get("bind", "")).strip()
    if not bind:
        _die("gunicorn.bind is required.")
    if not bind.startswith("127.0.0.1:") and not bind.startswith("unix:"):
        _die(
            'gunicorn.bind must start with "127.0.0.1:" or "unix:" '
            "(Gunicorn must not listen on a public interface)."
        )

    caddy = cfg["caddy"]
    if not isinstance(caddy, dict):
        _die("caddy must be an object.")
    for ck in ("sites_dir", "fragment_name"):
        if not str(caddy.get(ck, "")).strip():
            _die(f"caddy.{ck} is required.")

    if "*" in hosts:
        _die("django.allowed_hosts must not contain '*'.")

    domain = str(cfg["domain"]).strip()
    if not domain or re.search(r"[\s/]", domain):
        _die("domain must be a non-empty hostname without spaces or slashes.")

    for key in ("idempotent", "strict_port_check"):
        if key in cfg and not isinstance(cfg[key], bool):
            _die(f"{key} must be a boolean when set.")


def validate_deployment_paths(cfg: dict[str, Any]) -> None:
    """Reject dangerous or inconsistent paths before touching the filesystem."""
    paths = cfg["paths"]
    base = Path(paths["base"]).expanduser().resolve()
    app = Path(paths["app"]).expanduser().resolve()
    venv = Path(paths["venv"]).expanduser().resolve()
    static = Path(paths["static"]).expanduser().resolve()
    media = Path(paths["media"]).expanduser().resolve()
    env_file = Path(paths["env_file"]).expanduser()

    for label, p in ("paths.base", base), ("paths.app", app), ("paths.venv", venv), ("paths.static", static), (
        "paths.media",
        media,
    ):
        if not p.is_absolute():
            _die(f"{label} must be an absolute path, got {p}")
    if not env_file.is_absolute():
        _die(f"paths.env_file must be absolute, got {env_file}")

    forbidden = {
        Path("/").resolve(),
        Path("/bin").resolve(),
        Path("/sbin").resolve(),
        Path("/lib").resolve(),
        Path("/etc").resolve(),
    }
    if base in forbidden or app in forbidden or venv in forbidden:
        _die("paths.base / paths.app / paths.venv must not point at a critical system directory.")

    def _is_under(parent: Path, child: Path) -> bool:
        try:
            child.resolve().relative_to(parent.resolve())
            return True
        except ValueError:
            return False

    if not _is_under(base, app):
        _warn(f"paths.app ({app}) is not under paths.base ({base}); continuing (unusual layout).")
    if not _is_under(base, venv):
        _warn(f"paths.venv ({venv}) is not under paths.base ({base}); continuing (unusual layout).")

    parent = env_file.parent
    if not parent.exists():
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            _die(f"Cannot create directory for env file {parent}: {e}")
    if not os.access(str(parent), os.W_OK):
        _die(f"paths.env_file parent is not writable: {parent}")


# ---------------------------------------------------------------------------
# OS / privileges
# ---------------------------------------------------------------------------


def require_root() -> None:
    if os.geteuid() != 0:
        _die(
            "This installer must run as root. Use: "
            "sudo bash install.sh --config /path/to/config.json   OR   "
            "sudo python3 install/install.py --config /path/to/config.json"
        )


def check_ubuntu_24() -> None:
    os_release = Path("/etc/os-release")
    if not os_release.is_file():
        _die("/etc/os-release not found; unsupported OS.")
    text = os_release.read_text(encoding="utf-8", errors="replace")
    data: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            data[k.strip()] = v.strip().strip('"')
    if data.get("ID") != "ubuntu":
        _die(f'Expected ID=ubuntu in /etc/os-release, got ID={data.get("ID")!r}.')
    vid = data.get("VERSION_ID", "")
    if vid != "24.04":
        _die(
            f"This installer targets Ubuntu 24.04 (VERSION_ID=24.04). "
            f"Found VERSION_ID={vid!r}. Aborting."
        )


def run(cmd: list[str], *, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    print(f"+ {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, text=True, capture_output=False, env=env)


def run_capture(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=check, text=True, capture_output=True)


# ---------------------------------------------------------------------------
# Paths / copy
# ---------------------------------------------------------------------------


def path_is_nonempty_dir(p: Path) -> bool:
    if not p.is_dir():
        return False
    try:
        next(p.iterdir())
    except StopIteration:
        return False
    return True


def fresh_bundled_git_checkout_at_base(cfg: dict[str, Any]) -> bool:
    """
    True when paths.base is a non-empty repo root (manage.py + install/) but
    paths.app is not populated yet. Typical: git clone into /opt/recharge-desk
    with default paths.base == clone root and paths.app == .../app.
    """
    proj = cfg.get("project")
    if not isinstance(proj, dict) or str(proj.get("mode", "bundled")) != "bundled":
        return False
    base = Path(cfg["paths"]["base"]).resolve()
    app = Path(cfg["paths"]["app"]).resolve()
    if app == base:
        return False
    try:
        app.relative_to(base)
    except ValueError:
        return False
    if not (base / "manage.py").is_file():
        return False
    if not (base / "install" / "install.py").is_file():
        return False
    if (app / "manage.py").is_file():
        return False
    return True


def assert_install_target_safe(cfg: dict[str, Any]) -> None:
    base = Path(cfg["paths"]["base"])
    app = Path(cfg["paths"]["app"])
    force = bool(cfg.get("force", False))
    idempotent = bool(cfg.get("idempotent", False))
    if path_is_nonempty_dir(base) and not force:
        if idempotent and (app / "manage.py").is_file():
            print(
                f"[idempotent] Non-empty {base} with existing app at {app}; "
                "will skip re-copying project sources (upgrade / re-run)."
            )
            return
        if fresh_bundled_git_checkout_at_base(cfg):
            print(
                f"[install] Non-empty {base} looks like a fresh git checkout; "
                f"will copy sources into {app} (first install)."
            )
            return
        _die(
            f"Refusing to install: {base} exists and is non-empty. "
            f'Set "force": true to replace, or "idempotent": true to re-run against an existing app tree.'
        )


def should_skip_project_copy(cfg: dict[str, Any], app: Path, dry_run: bool) -> bool:
    if dry_run:
        return False
    if bool(cfg.get("force", False)):
        return False
    if bool(cfg.get("idempotent", False)) and (app / "manage.py").is_file():
        print(f"[idempotent] Skipping project copy/clone; using {app}")
        return True
    return False


def ignore_copy_patterns(dir_name: str, names: list[str]) -> set[str]:
    skip = {".venv", "venv", "__pycache__", ".git", ".cursor"}
    return {n for n in names if n in skip}


def copy_project_bundled(dest_app: Path, dry_run: bool) -> None:
    if not (REPO_ROOT / "manage.py").is_file():
        _die(f"bundled mode expects manage.py under {REPO_ROOT}")
    if dry_run:
        print(f"[dry-run] Would copy project from {REPO_ROOT} to {dest_app}")
        return
    dest_app.parent.mkdir(parents=True, exist_ok=True)
    if dest_app.exists():
        shutil.rmtree(dest_app)
    shutil.copytree(REPO_ROOT, dest_app, ignore=ignore_copy_patterns)


def clone_project_git(dest_app: Path, url: str, ref: str, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] Would git clone {url} @ {ref} into {dest_app}")
        return
    parent = dest_app.parent
    parent.mkdir(parents=True, exist_ok=True)
    if dest_app.exists():
        shutil.rmtree(dest_app)
    run(["git", "clone", "--depth", "1", "--branch", ref, url, str(dest_app)])


def copy_project_local(dest_app: Path, src: Path, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] Would copy from {src} to {dest_app}")
        return
    if dest_app.exists():
        shutil.rmtree(dest_app)
    shutil.copytree(src, dest_app, ignore=ignore_copy_patterns)


# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------


def sql_literal(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def dollar_quote(s: str) -> str:
    """PostgreSQL dollar-quoted string with a tag that does not appear in *s*."""
    for _ in range(32):
        tag = "bq_" + secrets.token_hex(8)
        delim = f"${tag}$"
        if delim not in s:
            return delim + s + delim
    _die("Could not build dollar-quoted SQL string for password.")


def postgres_role_exists(role: str) -> bool:
    r = run_capture(
        [
            "sudo",
            "-u",
            "postgres",
            "psql",
            "-tAc",
            f"SELECT 1 FROM pg_roles WHERE rolname = {sql_literal(role)}",
        ],
        check=False,
    )
    return r.returncode == 0 and r.stdout.strip() == "1"


def postgres_database_owner(dbname: str) -> str | None:
    r = run_capture(
        [
            "sudo",
            "-u",
            "postgres",
            "psql",
            "-tAc",
            "SELECT rolname FROM pg_roles WHERE oid = "
            f"(SELECT datdba FROM pg_database WHERE datname = {sql_literal(dbname)})",
        ],
        check=False,
    )
    if r.returncode != 0:
        return None
    out = r.stdout.strip()
    return out or None


def postgres_apply(cfg: dict[str, Any], dry_run: bool) -> None:
    pg = cfg["postgres"]
    if not pg.get("create_database", True):
        print("postgres.create_database is false; skipping CREATE USER/DATABASE.")
        return
    dbname = str(pg["name"])
    user = str(pg["user"])
    password = str(pg["password"])
    pw_dq = dollar_quote(password)
    role_sql = f"""DO $$
DECLARE
  role_nm text := {sql_literal(user)};
  pw text := {pw_dq};
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_nm) THEN
    EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L', role_nm, pw);
  ELSE
    EXECUTE format('ALTER ROLE %I PASSWORD %L', role_nm, pw);
  END IF;
END $$;"""
    if dry_run:
        print("[dry-run] Would create/update PostgreSQL role and database.")
        return
    run(["systemctl", "start", "postgresql"])
    run(["systemctl", "enable", "postgresql"])

    existed_before = postgres_role_exists(user)
    print(
        f"[postgres] Role {user!r}: "
        + ("exists (password will be updated if needed)" if existed_before else "will be created")
    )
    run(["sudo", "-u", "postgres", "psql", "-v", "ON_ERROR_STOP=1", "-c", role_sql])

    chk = run_capture(
        [
            "sudo",
            "-u",
            "postgres",
            "psql",
            "-tAc",
            f"SELECT 1 FROM pg_database WHERE datname = {sql_literal(dbname)}",
        ],
        check=False,
    )
    exists = chk.returncode == 0 and chk.stdout.strip() == "1"
    if exists:
        owner = postgres_database_owner(dbname)
        print(f"[postgres] Database {dbname!r} already exists (owner={owner!r}).")
        if owner and owner != user:
            _die(
                f"PostgreSQL database {dbname!r} exists but is owned by role {owner!r}, "
                f"not {user!r}. Rename config, drop the DB manually, or align postgres.name / postgres.user."
            )
    else:
        print(f"[postgres] Creating database {dbname!r} owned by {user!r}.")
        run(
            [
                "sudo",
                "-u",
                "postgres",
                "psql",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                f"CREATE DATABASE {dbname} OWNER {user};",
            ]
        )
    run(
        [
            "sudo",
            "-u",
            "postgres",
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            f"GRANT ALL PRIVILEGES ON DATABASE {dbname} TO {user};",
        ]
    )


# ---------------------------------------------------------------------------
# Environment file (systemd EnvironmentFile= compatible)
# ---------------------------------------------------------------------------


def write_env_file(path: Path, values: dict[str, str], dry_run: bool) -> None:
    lines = [
        "# Generated by install/install.py — do not commit.",
        "# Trust: Gunicorn must bind loopback or unix socket only; Caddy is the public edge.",
        "",
    ]
    for k in sorted(values.keys()):
        v = values[k]
        esc = v.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{k}="{esc}"')
    text = "\n".join(lines) + "\n"
    if dry_run:
        print(f"[dry-run] Would write {path} ({len(text)} bytes)")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".env-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def build_env_dict(cfg: dict[str, Any], secret_key: str) -> dict[str, str]:
    paths = cfg["paths"]
    pg = cfg["postgres"]
    dj = cfg["django"]
    return {
        "DJANGO_SETTINGS_MODULE": "config.settings.production",
        "DJANGO_SECRET_KEY": secret_key,
        "DJANGO_ALLOWED_HOSTS": ",".join(dj["allowed_hosts"]),
        "DJANGO_CSRF_TRUSTED_ORIGINS": ",".join(dj["csrf_trusted_origins"]),
        "POSTGRES_DB": str(pg["name"]),
        "POSTGRES_USER": str(pg["user"]),
        "POSTGRES_PASSWORD": str(pg["password"]),
        "POSTGRES_HOST": str(pg["host"]),
        "POSTGRES_PORT": str(int(pg["port"])),
        "DJANGO_STATIC_ROOT": paths["static"],
        "DJANGO_MEDIA_ROOT": paths["media"],
        "DJANGO_LOG_LEVEL": str(dj.get("log_level", "INFO")),
    }


# ---------------------------------------------------------------------------
# Python venv + Django
# ---------------------------------------------------------------------------


def create_venv_and_install(cfg: dict[str, Any], dry_run: bool) -> None:
    venv = Path(cfg["paths"]["venv"])
    app = Path(cfg["paths"]["app"])
    req = app / "requirements.txt"
    if not req.is_file():
        _die(f"Missing requirements.txt at {req}")
    if dry_run:
        print(f"[dry-run] Would create venv at {venv} and pip install -r requirements.txt")
        return
    pip = str(venv / "bin" / "pip")
    idem = bool(cfg.get("idempotent", False)) and not bool(cfg.get("force", False))
    if venv.exists() and idem and Path(pip).is_file():
        print(f"[idempotent] Reusing venv at {venv}; running pip install -r only.")
        run([pip, "install", "--upgrade", "pip", "setuptools", "wheel"])
        run([pip, "install", "-r", str(req)])
        return
    if venv.exists():
        shutil.rmtree(venv)
    run([sys.executable, "-m", "venv", str(venv)])
    run([pip, "install", "--upgrade", "pip", "setuptools", "wheel"])
    run([pip, "install", "-r", str(req)])


def _sudo_user_env_cmd(
    system_user: str,
    env_dict: dict[str, str],
    inner: list[str],
) -> list[str]:
    """Run *inner* as *system_user* with *env_dict* exported into the child (via ``env``)."""
    merged = dict(env_dict)
    merged.setdefault("PATH", os.environ.get("PATH", "/usr/bin:/bin"))
    env_pairs = [f"{k}={v}" for k, v in sorted(merged.items())]
    return ["sudo", "-u", system_user, "-H", "env", *env_pairs, *inner]


def django_migrate_collectstatic(cfg: dict[str, Any], env_dict: dict[str, str], dry_run: bool) -> None:
    app = Path(cfg["paths"]["app"])
    venv_python = Path(cfg["paths"]["venv"]) / "bin" / "python"
    user = str(cfg["system_user"])
    static_root = Path(cfg["paths"]["static"])
    if dry_run:
        print("[dry-run] Would run migrate and collectstatic as app user.")
        return
    env_run = dict(env_dict)
    env_run.setdefault("HOME", str(Path(cfg["paths"]["base"])))
    run(
        _sudo_user_env_cmd(
            user,
            env_run,
            [str(venv_python), str(app / "manage.py"), "migrate", "--noinput"],
        ),
        cwd=str(app),
    )
    run(
        _sudo_user_env_cmd(
            user,
            env_run,
            [str(venv_python), str(app / "manage.py"), "collectstatic", "--noinput"],
        ),
        cwd=str(app),
    )
    run(["chown", "-R", f"{user}:{user}", str(static_root)])


def verify_django_production(cfg: dict[str, Any], env_dict: dict[str, str], dry_run: bool) -> None:
    if dry_run:
        print("[dry-run] Would verify Django production settings import.")
        return
    app = Path(cfg["paths"]["app"])
    venv_python = Path(cfg["paths"]["venv"]) / "bin" / "python"
    env_run = dict(env_dict)
    env_run.setdefault("HOME", str(Path(cfg["paths"]["base"])))
    env_run["PYTHONPATH"] = str(app)
    code = (
        "import os, django;\n"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings.production');\n"
        "django.setup();\n"
        "from django.conf import settings;\n"
        "assert not settings.DEBUG;\n"
        "assert settings.DATABASES['default']['ENGINE'].endswith('postgresql');\n"
        "print('django_ok', settings.ALLOWED_HOSTS[0])\n"
    )
    run(
        _sudo_user_env_cmd(
            str(cfg["system_user"]),
            env_run,
            [str(venv_python), "-c", code],
        ),
        cwd=str(app),
    )


# ---------------------------------------------------------------------------
# System user + directories
# ---------------------------------------------------------------------------


def ensure_system_user(name: str, home: Path, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] Would ensure system user {name} with home {home}")
        return
    try:
        run_capture(["id", "-u", name], check=True)
    except subprocess.CalledProcessError:
        run(
            [
                "useradd",
                "--system",
                "--no-create-home",
                "--home-dir",
                str(home),
                "--shell",
                "/usr/sbin/nologin",
                name,
            ]
        )


def prepare_directories(cfg: dict[str, Any], dry_run: bool) -> None:
    paths = cfg["paths"]
    base = Path(paths["base"])
    static = Path(paths["static"])
    media = Path(paths["media"])
    if dry_run:
        print(f"[dry-run] Would mkdir {base}, {static}, {media}")
        return
    base.mkdir(parents=True, exist_ok=True)
    static.mkdir(parents=True, exist_ok=True)
    media.mkdir(parents=True, exist_ok=True)


def chown_deploy_tree(cfg: dict[str, Any], dry_run: bool) -> None:
    if dry_run:
        return
    base = Path(cfg["paths"]["base"])
    user = str(cfg["system_user"])
    if not base.is_dir():
        _die(f"chown: paths.base is not a directory: {base}")
    try:
        run_capture(["id", "-u", user], check=True)
    except subprocess.CalledProcessError:
        _die(f"chown: system user {user!r} does not exist; cannot chown {base}.")
    run(["chown", "-R", f"{user}:{user}", str(base)])


# ---------------------------------------------------------------------------
# systemd + Caddy
# ---------------------------------------------------------------------------


def render_systemd_unit(cfg: dict[str, Any]) -> str:
    paths = cfg["paths"]
    gun = cfg["gunicorn"]
    svc = str(cfg["service_name"])
    user = str(cfg["system_user"])
    return f"""[Unit]
Description=Recharge Desk Gunicorn ({svc})
After=network.target postgresql.service

[Service]
Type=simple
User={user}
Group={user}
WorkingDirectory={paths["app"]}
EnvironmentFile={paths["env_file"]}
ExecStart={paths["venv"]}/bin/gunicorn \\
    --bind {gun["bind"]} \\
    --workers {int(gun["workers"])} \\
    --timeout {int(gun["timeout"])} \\
    config.wsgi:application
Restart=on-failure

[Install]
WantedBy=multi-user.target
"""


def render_caddy_fragment(cfg: dict[str, Any]) -> str:
    domain = str(cfg["domain"])
    paths = cfg["paths"]
    gun = cfg["gunicorn"]
    bind = str(gun["bind"])
    if bind.startswith("unix:"):
        sock_path = bind.split(":", 1)[1].removeprefix("//")
        proxy_line = f"\treverse_proxy unix//{sock_path}\n"
    else:
        proxy_line = f"\treverse_proxy {bind}\n"
    return f"""# Generated by install/install.py for {domain}
{domain} {{
\tencode gzip

\thandle_path /static/* {{
\t\troot * {paths["static"]}
\t\tfile_server
\t}}

\thandle_path /media/* {{
\t\troot * {paths["media"]}
\t\tfile_server
\t}}

{proxy_line}
}}
"""


def write_systemd_unit(cfg: dict[str, Any], dry_run: bool) -> Path:
    svc = str(cfg["service_name"])
    unit_path = Path(f"/etc/systemd/system/{svc}.service")
    content = render_systemd_unit(cfg)
    if dry_run:
        print(f"[dry-run] Would write {unit_path}")
        return unit_path
    unit_path.write_text(content, encoding="utf-8")
    os.chmod(unit_path, 0o644)
    return unit_path


def write_caddy_fragment(cfg: dict[str, Any], dry_run: bool) -> tuple[Path, Path]:
    caddy = cfg["caddy"]
    sites_dir = Path(str(caddy["sites_dir"]))
    frag = sites_dir / str(caddy["fragment_name"])
    content = render_caddy_fragment(cfg)
    if dry_run:
        print(f"[dry-run] Would write {frag}")
        return frag, Path("/etc/caddy/Caddyfile")
    sites_dir.mkdir(parents=True, exist_ok=True)
    frag.write_text(content, encoding="utf-8")
    os.chmod(frag, 0o644)
    return frag, Path("/etc/caddy/Caddyfile")


def ensure_caddy_sites_import(caddy_cfg: dict[str, Any], dry_run: bool) -> bool:
    """Returns True if /etc/caddy/Caddyfile was modified (caller may need rollback guidance)."""
    if not caddy_cfg.get("ensure_sites_import", True):
        print("caddy.ensure_sites_import is false; skipping Caddyfile import line.")
        return False
    caddyfile = Path("/etc/caddy/Caddyfile")
    import_line = "import /etc/caddy/sites/*.caddy"
    if not caddyfile.is_file():
        print(f"WARNING: {caddyfile} missing; create it manually with: {import_line}")
        return False
    text = caddyfile.read_text(encoding="utf-8", errors="replace")
    if import_line in text:
        print("Caddyfile already imports /etc/caddy/sites/*.caddy")
        return False
    if dry_run:
        print(f"[dry-run] Would prepend import line to {caddyfile} (backup .bak.installer)")
        return False
    bak = caddyfile.with_suffix(caddyfile.suffix + ".bak.installer")
    shutil.copy2(caddyfile, bak)
    new_text = import_line + "\n\n" + text
    caddyfile.write_text(new_text, encoding="utf-8")
    print(f"Prepended import to {caddyfile}; backup at {bak}")
    return True


def check_acme_listen_ports(cfg: dict[str, Any], dry_run: bool) -> None:
    """Warn or abort if TCP 80/443 are bound by a process other than Caddy (ACME / HTTPS)."""
    if dry_run:
        return
    strict = bool(cfg.get("strict_port_check", False))
    for port in (80, 443):
        r = run_capture(["ss", "-H", "-ltnp", f"sport = :{port}"], check=False)
        if r.returncode != 0:
            _warn(f"Could not query listeners on port {port} (ss failed); skipping port check.")
            continue
        lines = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
        if not lines:
            continue
        for ln in lines:
            low = ln.lower()
            if "caddy" in low:
                continue
            msg = f"Port {port} is in use (not obviously Caddy): {ln}"
            if strict:
                _die(
                    msg
                    + "  Free the port or set \"strict_port_check\": false after review. "
                    "Caddy needs 80/443 for automatic HTTPS unless you use DNS challenge elsewhere."
                )
            _warn(msg)


def systemd_reload_and_start(service: str, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] Would systemctl enable --now {service}")
        return
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", f"{service}.service"])
    run(["systemctl", "restart", f"{service}.service"])


def reload_caddy(dry_run: bool) -> None:
    if dry_run:
        print("[dry-run] Would reload caddy")
        return
    run(["systemctl", "reload", "caddy"], check=False)


# ---------------------------------------------------------------------------
# Optional DNS check
# ---------------------------------------------------------------------------


def optional_dns_check(cfg: dict[str, Any]) -> None:
    if not cfg.get("dns_check", True):
        return
    domain = str(cfg["domain"])
    try:
        resolved = socket.gethostbyname(domain)
    except OSError as e:
        print(f"WARNING: DNS lookup failed for {domain}: {e}")
        return
    public_ip = None
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=5) as r:
            public_ip = r.read().decode("utf-8").strip()
    except (urllib.error.URLError, OSError) as e:
        print(f"WARNING: Could not detect public IPv4 (ipify): {e}")
    print(f"DNS: {domain} -> {resolved}")
    if public_ip:
        print(f"This host's detected public IPv4: {public_ip}")
        if resolved != public_ip:
            print(
                "WARNING: Resolved address does not match this host's public IP "
                "(NAT, multiple interfaces, or DNS not pointed here yet). "
                "Caddy ACME may fail until DNS is correct."
            )


# ---------------------------------------------------------------------------
# APT
# ---------------------------------------------------------------------------


def apt_install_packages(dry_run: bool) -> None:
    pkgs = [
        "python3",
        "python3-venv",
        "python3-dev",
        "build-essential",
        "libpq-dev",
        "postgresql",
        "postgresql-client",
        "git",
        "curl",
        "caddy",
    ]
    if dry_run:
        print(f"[dry-run] Would apt-get install -y {' '.join(pkgs)}")
        return
    run(["apt-get", "update", "-y"])
    run(["apt-get", "install", "-y", *pkgs])


# ---------------------------------------------------------------------------
# Post checks
# ---------------------------------------------------------------------------


def print_service_status(service: str) -> None:
    r = run_capture(["systemctl", "is-active", f"{service}.service"], check=False)
    print(f"systemctl is-active {service}.service -> {r.stdout.strip() or r.stderr.strip()}")


def curl_check_once(url: str, *, extra_args: list[str] | None = None) -> tuple[str, str]:
    cmd = ["curl", "-fsS", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "15"]
    if extra_args:
        cmd.extend(extra_args)
    cmd.append(url)
    r = run_capture(cmd, check=False)
    code = (r.stdout or "").strip()
    return code, (r.stderr or "").strip()


def curl_check_with_retries(
    label: str,
    url: str,
    *,
    extra_args: list[str] | None = None,
    retries: int = 5,
    delay_sec: float = 2.0,
    accept_prefixes: tuple[str, ...] = ("2", "3"),
) -> bool:
    """HTTP(S) health probe with retries (service / ACME may need a warm-up)."""
    last_code, last_err = "", ""
    for attempt in range(1, retries + 1):
        last_code, last_err = curl_check_once(url, extra_args=extra_args)
        if last_code and last_code[0] in accept_prefixes:
            print(f"[health] {label} {url} -> HTTP {last_code} (attempt {attempt}/{retries})")
            return True
        print(
            f"[health] {label} {url} -> HTTP {last_code!r} (attempt {attempt}/{retries}); "
            f"retry in {delay_sec}s… stderr={last_err!r}"
        )
        if attempt < retries:
            time.sleep(delay_sec)
    print(f"[health] FAILED: {label} {url} after {retries} attempts (last HTTP={last_code!r}).", file=sys.stderr)
    return False


def curl_check_gunicorn(domain: str, bind: str, dry_run: bool) -> bool:
    if dry_run or not bind.startswith("127.0.0.1:"):
        return True
    ok = curl_check_with_retries(
        "gunicorn(loopback)",
        f"http://{bind}/",
        extra_args=["-H", f"Host: {domain}"],
        retries=4,
        delay_sec=1.5,
    )
    return ok


def set_env_file_permissions(env_path: Path, system_user: str, dry_run: bool) -> None:
    if dry_run:
        return
    run(["chown", f"root:{system_user}", str(env_path)])
    run(["chmod", "640", str(env_path)])


def main() -> None:
    parser = argparse.ArgumentParser(description="Recharge Desk Ubuntu 24 installer")
    parser.add_argument("--config", required=True, type=Path, help="Path to install JSON config")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions only; do not modify system (pre-flight + plan).",
    )
    args = parser.parse_args()
    cfg_path: Path = args.config
    cfg = load_config(cfg_path)
    validate_config(cfg)
    validate_deployment_paths(cfg)
    dry_run = bool(args.dry_run or cfg.get("dry_run", False))

    require_root()
    check_ubuntu_24()
    optional_dns_check(cfg)
    assert_install_target_safe(cfg)

    paths = cfg["paths"]
    base = Path(paths["base"])
    app = Path(paths["app"])
    user = str(cfg["system_user"])
    svc = str(cfg["service_name"])
    domain = str(cfg["domain"])
    caddyfile_modified = False
    phase = "pre-flight"

    try:
        phase = "apt"
        apt_install_packages(dry_run)
        check_acme_listen_ports(cfg, dry_run)

        phase = "filesystem"
        if cfg.get("force") and base.exists() and not dry_run:
            print(f"force=true: removing existing tree {base}")
            shutil.rmtree(base)

        prepare_directories(cfg, dry_run)
        ensure_system_user(user, base, dry_run)

        phase = "project"
        if not should_skip_project_copy(cfg, app, dry_run):
            mode = str(cfg["project"]["mode"])
            if mode == "bundled":
                copy_project_bundled(app, dry_run)
            elif mode == "git":
                clone_project_git(
                    app,
                    str(cfg["project"]["git_url"]),
                    str(cfg["project"].get("git_ref") or "main"),
                    dry_run,
                )
            else:
                copy_project_local(app, Path(str(cfg["project"]["local_path"])), dry_run)

        chown_deploy_tree(cfg, dry_run)

        phase = "postgres"
        postgres_apply(cfg, dry_run)

        phase = "django_env"
        dj = cfg["django"]
        secret = (dj.get("secret_key") or "").strip() if isinstance(dj.get("secret_key"), str) else ""
        if not secret:
            secret = secrets.token_urlsafe(64)
        env_dict = build_env_dict(cfg, secret)
        env_path = Path(paths["env_file"])
        write_env_file(env_path, env_dict, dry_run)
        set_env_file_permissions(env_path, user, dry_run)

        phase = "venv_migrate"
        create_venv_and_install(cfg, dry_run)
        chown_deploy_tree(cfg, dry_run)
        django_migrate_collectstatic(cfg, env_dict, dry_run)
        verify_django_production(cfg, env_dict, dry_run)

        phase = "systemd_caddy"
        write_systemd_unit(cfg, dry_run)
        write_caddy_fragment(cfg, dry_run)
        caddyfile_modified = bool(ensure_caddy_sites_import(cfg["caddy"], dry_run))

        systemd_reload_and_start(svc, dry_run)
        reload_caddy(dry_run)
        check_acme_listen_ports(cfg, dry_run)

        phase = "health"
        print("\n--- Post-install status ---")
        print_service_status(svc)
        print_service_status("caddy")
        if dry_run:
            print("[dry-run] Skipping live HTTP/HTTPS health checks.")
        else:
            curl_check_with_retries("https", f"https://{domain}/", retries=6, delay_sec=3.0)
            curl_check_with_retries(
                "http->https",
                f"http://{domain}/",
                retries=3,
                delay_sec=1.0,
            )
            curl_check_gunicorn(domain, str(cfg["gunicorn"]["bind"]), dry_run)
        print(f"\nDone. Site: https://{domain}/")
        if not dry_run:
            print("If HTTPS health checks failed: verify DNS, ports 80/443, and journalctl -u caddy.")
    except Exception as e:
        print_failure_recovery(
            phase=phase,
            err=e,
            cfg=cfg,
            caddyfile_backup_hint=caddyfile_modified,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()