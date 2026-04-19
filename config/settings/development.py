"""Local development defaults (SQLite, DEBUG on)."""

import os

from .base import *  # noqa: F403
from .base import split_env_csv

DEBUG = True

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-dev-only-not-for-production",
)

_allowed = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
ALLOWED_HOSTS = split_env_csv(_allowed) or ["localhost", "127.0.0.1"]

CSRF_TRUSTED_ORIGINS = split_env_csv(os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", ""))

# Default to SQLite for the zero-config dev experience. To exercise the
# production stack locally (recommended before each release), set
# POSTGRES_DB and the related env vars and the same dict shape will be
# built. This keeps `manage.py` working with no env at all while letting
# devs reproduce production query plans / migrations on demand.
if os.environ.get("POSTGRES_DB", "").strip():
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ["POSTGRES_DB"],
            "USER": os.environ.get("POSTGRES_USER", "recharge"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
            "HOST": os.environ.get("POSTGRES_HOST", "127.0.0.1"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": int(os.environ.get("POSTGRES_CONN_MAX_AGE", "60")),
            "OPTIONS": {},
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",  # noqa: F405
        }
    }

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
