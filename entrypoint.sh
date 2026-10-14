#!/usr/bin/env sh
set -e

case "$1" in
    worker|web|"") ;;
    *) echo "[entrypoint] unknown mode: $1 (expected: web | worker)" >&2; exit 2 ;;
esac

echo "[entrypoint] Ensuring media directory exists..."
mkdir -p "${DJANGO_MEDIA_ROOT:-/app/media}"

echo "[entrypoint] Checking database backend..."
python - <<'PY'
import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")
try:
    import django

    django.setup()
    from django.conf import settings

    db = settings.DATABASES["default"]
    engine = db.get("ENGINE", "")
    host = db.get("HOST") or "(local)"
    name = db.get("NAME", "")
    print(f"[entrypoint] Database engine={engine} host={host} name={name}", flush=True)
    if "sqlite" in engine:
        print(
            "[entrypoint] ERROR: SQLite is not persistent across container rebuilds. "
            "Set DATABASE_URL to your Coolify Postgres internal URL.",
            file=sys.stderr,
            flush=True,
        )
        sys.exit(1)
except Exception as exc:
    print(f"[entrypoint] Database check failed: {exc}", file=sys.stderr, flush=True)
    sys.exit(1)
PY

if [ "$1" = "worker" ]; then
    # Celery worker with embedded beat (-B). Migrations/collectstatic are the
    # web service's job. Run exactly ONE replica of this service: every extra
    # replica would run its own beat and duplicate the periodic tasks.
    echo "[entrypoint] Starting celery worker (queues: refresh,charge,reports)"
    exec celery -A config worker -B \
        -Q refresh,charge,reports \
        --concurrency ${CELERY_CONCURRENCY:-4} \
        --loglevel INFO
fi

echo "[entrypoint] Running migrations..."
python manage.py migrate --noinput

echo "[entrypoint] Collecting static files..."
python manage.py collectstatic --noinput

echo "[entrypoint] Starting gunicorn on 0.0.0.0:${PORT:-8000}"
# ``--threads`` only applies to the ``gthread`` worker class; the default
# ``sync`` workers ignore it and handle one request at a time per worker.
exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:${PORT:-8000} \
    --worker-class ${GUNICORN_WORKER_CLASS:-gthread} \
    --workers ${GUNICORN_WORKERS:-3} \
    --threads ${GUNICORN_THREADS:-2} \
    --timeout ${GUNICORN_TIMEOUT:-60} \
    --access-logfile - \
    --error-logfile -
