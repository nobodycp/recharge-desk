"""
Production settings: PostgreSQL, HTTPS via reverse proxy (e.g. Caddy), no DEBUG.

Forwarded headers: this module sets ``SECURE_PROXY_SSL_HEADER`` and
``USE_X_FORWARDED_HOST``. Django will treat requests as secure and use the
forwarded host only if those headers are set by a **trusted** reverse proxy
(e.g. Caddy). The application server (Gunicorn) must **not** be reachable
directly from untrusted clients on the same listener that honors these headers:
bind Gunicorn to ``127.0.0.1`` or a Unix socket and terminate TLS / enforce policy
at the proxy. Exposing Gunicorn on a public interface while trusting forwarded
headers would allow header spoofing and weaken HTTPS/host guarantees.

When the Django test runner is invoked (``test`` in ``sys.argv``), this module
switches to an in-memory SQLite database so CI or local checks do not require
PostgreSQL credentials.
"""

from __future__ import annotations

import os
import sys

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ImproperlyConfigured(
            f"Production settings require the {name} environment variable to be set."
        )
    return value


_IS_DJANGO_TEST = "test" in sys.argv

if _IS_DJANGO_TEST:
    DEBUG = False
    SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "test-secret-not-for-production")
    ALLOWED_HOSTS = ["testserver"]
    CSRF_TRUSTED_ORIGINS = []
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ":memory:",
        }
    }
    PASSWORD_HASHERS = [
        "django.contrib.auth.hashers.MD5PasswordHasher",
    ]
    EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
else:
    DEBUG = False

    SECRET_KEY = _require("DJANGO_SECRET_KEY")

    hosts_raw = _require("DJANGO_ALLOWED_HOSTS")
    ALLOWED_HOSTS = [h.strip() for h in hosts_raw.split(",") if h.strip()]
    if "*" in ALLOWED_HOSTS:
        raise ImproperlyConfigured(
            "DJANGO_ALLOWED_HOSTS must not contain '*' in production. "
            "List explicit hostnames (e.g. s.prosim.ps)."
        )

    csrf_raw = _require("DJANGO_CSRF_TRUSTED_ORIGINS")
    CSRF_TRUSTED_ORIGINS = [o.strip() for o in csrf_raw.split(",") if o.strip()]
    for origin in CSRF_TRUSTED_ORIGINS:
        if not origin.startswith("https://"):
            raise ImproperlyConfigured(
                "DJANGO_CSRF_TRUSTED_ORIGINS must use https:// origins in production "
                f"(invalid entry: {origin!r})."
            )

    # Coolify (and most 12-factor PaaS) exposes a single DATABASE_URL. When
    # present we use it directly; otherwise we fall back to the discrete
    # POSTGRES_* variables that the previous deploy flow already supported.
    _database_url = os.environ.get("DATABASE_URL", "").strip()
    if _database_url:
        DATABASES = {
            "default": dj_database_url.parse(
                _database_url,
                conn_max_age=int(os.environ.get("POSTGRES_CONN_MAX_AGE", "300")),
                ssl_require=os.environ.get("POSTGRES_SSLMODE", "").lower() == "require",
            ),
        }
    else:
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": _require("POSTGRES_DB"),
                "USER": _require("POSTGRES_USER"),
                "PASSWORD": _require("POSTGRES_PASSWORD"),
                "HOST": os.environ.get("POSTGRES_HOST", "127.0.0.1"),
                "PORT": os.environ.get("POSTGRES_PORT", "5432"),
                "CONN_MAX_AGE": int(os.environ.get("POSTGRES_CONN_MAX_AGE", "300")),
                "OPTIONS": {},
            }
        }
        if sslmode := os.environ.get("POSTGRES_SSLMODE", "").strip():
            DATABASES["default"]["OPTIONS"]["sslmode"] = sslmode

    # SQLite inside the container filesystem is wiped on every Coolify
    # redeploy; phone_refresh singleton settings would reset to migration
    # defaults after each build. Require a persistent PostgreSQL service.
    _engine = DATABASES["default"]["ENGINE"]
    if "sqlite" in _engine:
        raise ImproperlyConfigured(
            "Production must use PostgreSQL, not SQLite. "
            "Point DATABASE_URL at your Coolify Postgres service (internal URL). "
            "A sqlite file inside the app container is ephemeral and will lose "
            "admin settings on every redeploy."
        )

    # WhiteNoise: compressed (gzip + brotli) static storage. We avoid the
    # ``CompressedManifestStaticFilesStorage`` variant because its hashed
    # post-processing fails when CSS files reference assets we don't ship
    # (e.g. third-party ``.map`` files inside ``bootstrap.rtl.min.css``).
    # Compression alone is sufficient for our deployment; the reverse proxy
    # adds long-cache headers.
    STATICFILES_STORAGE = "whitenoise.storage.CompressedStaticFilesStorage"
    # Belt-and-braces: tell WhiteNoise's manifest lookup not to be strict in
    # case we ever switch back to the manifest variant.
    WHITENOISE_MANIFEST_STRICT = False
    # Long browser cache for compressed static assets (Cloudflare also caches).
    WHITENOISE_MAX_AGE = int(os.environ.get("WHITENOISE_MAX_AGE", str(60 * 60 * 24 * 30)))

    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True
    # Coolify terminates TLS and Cloudflare sits in front; trust forwarded
    # client IP headers in production. Set TRUST_X_FORWARDED_FOR=0 to disable.
    TRUST_FORWARDED_FOR = os.environ.get("TRUST_X_FORWARDED_FOR", "1").lower() in (
        "1",
        "true",
        "yes",
    )

    SECURE_SSL_REDIRECT = os.environ.get("DJANGO_SECURE_SSL_REDIRECT", "").lower() in (
        "1",
        "true",
        "yes",
    )
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    CSRF_COOKIE_HTTPONLY = True

    SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "0"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = (
        os.environ.get("SECURE_HSTS_INCLUDE_SUBDOMAINS", "").lower()
        in ("1", "true", "yes")
    )
    SECURE_HSTS_PRELOAD = os.environ.get("SECURE_HSTS_PRELOAD", "").lower() in (
        "1",
        "true",
        "yes",
    )

    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "simple": {
                "format": "{levelname} {asctime} {name} {message}",
                "style": "{",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "stream": sys.stdout,
                "formatter": "simple",
            },
        },
        "root": {
            "handlers": ["console"],
            "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO"),
        },
        "loggers": {
            "django.request": {
                "handlers": ["console"],
                "level": "WARNING",
                "propagate": False,
            },
        },
    }
