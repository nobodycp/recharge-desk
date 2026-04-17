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

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",  # noqa: F405
    }
}

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
