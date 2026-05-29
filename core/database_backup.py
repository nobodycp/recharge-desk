"""Database export/import for management settings.

Uses Django ``dumpdata`` / ``loaddata`` so backups work inside the app
container without ``pg_dump``. SQLite deployments can also download or
replace the raw ``db.sqlite3`` file.
"""

from __future__ import annotations

import gzip
import io
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, connections
from django.db.models.signals import post_save
from django.utils.translation import gettext as _

# Sessions are excluded so a restore does not resurrect stale login cookies.
_DUMP_EXCLUDES = ("sessions",)


def engine_info() -> dict:
    db = settings.DATABASES["default"]
    engine = db.get("ENGINE", "")
    name = db.get("NAME", "")
    return {
        "engine": engine,
        "name": str(name),
        "is_sqlite": "sqlite" in engine,
        "is_postgresql": "postgresql" in engine,
        "label": _engine_label(engine),
    }


def _engine_label(engine: str) -> str:
    if "postgresql" in engine:
        return str(_("PostgreSQL"))
    if "sqlite" in engine:
        return str(_("SQLite"))
    return engine.rsplit(".", maxsplit=1)[-1]


def _stamp() -> str:
    return date.today().isoformat()


def export_bytes() -> tuple[bytes, str, str]:
    """Return ``(payload, filename, content_type)`` for a download response."""
    info = engine_info()
    stamp = _stamp()
    if info["is_sqlite"]:
        path = Path(info["name"])
        if not path.is_file():
            raise FileNotFoundError(_("Database file not found."))
        payload = path.read_bytes()
        return payload, f"recharge-desk-{stamp}.sqlite3", "application/x-sqlite3"

    if info["is_postgresql"] and pg_tools_available():
        payload, ext = _export_pg_custom()
        return payload, f"recharge-desk-{stamp}{ext}", "application/octet-stream"

    payload = _export_fixture_gz()
    return payload, f"recharge-desk-{stamp}.json.gz", "application/gzip"


def _export_fixture_gz() -> bytes:
    text = io.StringIO()
    call_command(
        "dumpdata",
        natural_foreign=True,
        natural_primary=True,
        exclude=_DUMP_EXCLUDES,
        indent=2,
        stdout=text,
    )
    buf = io.BytesIO()
    with gzip.open(buf, "wb") as gz:
        gz.write(text.getvalue().encode("utf-8"))
    return buf.getvalue()


def pg_tools_available() -> bool:
    return shutil.which("pg_dump") is not None and shutil.which("pg_restore") is not None


def _export_pg_custom() -> tuple[bytes, str]:
    db = settings.DATABASES["default"]
    env = os.environ.copy()
    password = db.get("PASSWORD") or ""
    if password:
        env["PGPASSWORD"] = password
    with tempfile.NamedTemporaryFile(suffix=".dump", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        subprocess.run(
            [
                "pg_dump",
                "--host",
                str(db.get("HOST") or "127.0.0.1"),
                "--port",
                str(db.get("PORT") or "5432"),
                "--username",
                str(db.get("USER") or ""),
                "--dbname",
                str(db.get("NAME") or ""),
                "--format=custom",
                "--no-owner",
                "--no-privileges",
                f"--file={tmp_path}",
            ],
            check=True,
            env=env,
            capture_output=True,
        )
        return Path(tmp_path).read_bytes(), ".dump"
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(stderr or str(exc)) from exc
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def import_upload(uploaded, *, filename: str) -> None:
    """Replace the current database from an uploaded backup file."""
    name = (filename or uploaded.name or "").lower()
    info = engine_info()

    if info["is_sqlite"] and name.endswith(".sqlite3"):
        _import_sqlite_file(uploaded.read())
        return

    if name.endswith(".dump"):
        if not info["is_postgresql"]:
            raise ValueError(_("PostgreSQL dump files require a PostgreSQL database."))
        _import_pg_custom(uploaded.read())
        return

    if name.endswith(".json.gz") or name.endswith(".json"):
        _import_fixture(uploaded.read(), gzipped=name.endswith(".gz"))
        return

    raise ValueError(
        _("Unsupported file type. Use .json.gz, .json, .dump (PostgreSQL), or .sqlite3 (SQLite).")
    )


@contextmanager
def _suspend_user_profile_auto_create():
    """``loaddata`` loads ``auth.user`` before profiles; disable the signal that
    auto-creates a profile on user save, otherwise ``UserProfile`` rows collide."""
    from accounts.signals import ensure_profile

    User = get_user_model()
    post_save.disconnect(ensure_profile, sender=User)
    try:
        yield
    finally:
        post_save.connect(ensure_profile, sender=User)


def _import_fixture(raw: bytes, *, gzipped: bool) -> None:
    if gzipped:
        try:
            text = gzip.decompress(raw).decode("utf-8")
        except OSError as exc:
            raise ValueError(_("Invalid gzip archive.")) from exc
    else:
        text = raw.decode("utf-8")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tmp:
        tmp.write(text)
        path = tmp.name

    try:
        connection.close()
        call_command("flush", interactive=False, verbosity=0)
        with _suspend_user_profile_auto_create():
            call_command("loaddata", path, verbosity=0)
    except CommandError as exc:
        raise RuntimeError(str(exc)) from exc
    finally:
        Path(path).unlink(missing_ok=True)
        connection.close()


def _import_sqlite_file(raw: bytes) -> None:
    info = engine_info()
    if not info["is_sqlite"]:
        raise ValueError(_("SQLite files can only be imported into a SQLite database."))
    path = Path(info["name"])
    path.parent.mkdir(parents=True, exist_ok=True)
    connections["default"].close()
    backup = path.with_suffix(path.suffix + ".pre-import")
    if path.is_file():
        shutil.copy2(path, backup)
    path.write_bytes(raw)
    connections["default"].close()


def _import_pg_custom(raw: bytes) -> None:
    if not shutil.which("pg_restore"):
        raise RuntimeError(
            _("pg_restore is not installed on this server. Import a .json.gz backup instead.")
        )
    db = settings.DATABASES["default"]
    env = os.environ.copy()
    password = db.get("PASSWORD") or ""
    if password:
        env["PGPASSWORD"] = password

    with tempfile.NamedTemporaryFile(suffix=".dump", delete=False) as tmp:
        tmp.write(raw)
        dump_path = tmp.name

    try:
        subprocess.run(
            [
                "pg_restore",
                "--host",
                str(db.get("HOST") or "127.0.0.1"),
                "--port",
                str(db.get("PORT") or "5432"),
                "--username",
                str(db.get("USER") or ""),
                "--dbname",
                str(db.get("NAME") or ""),
                "--clean",
                "--if-exists",
                "--no-owner",
                "--no-privileges",
                dump_path,
            ],
            check=True,
            env=env,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(stderr or str(exc)) from exc
    finally:
        Path(dump_path).unlink(missing_ok=True)
    connection.close()
