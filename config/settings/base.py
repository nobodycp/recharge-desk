"""
Shared Django settings for all environments.

Environment-specific values belong in ``development.py`` or ``production.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.urls import reverse_lazy

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def split_env_csv(value: str | None) -> list[str]:
    if not value or not value.strip():
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "core",
    "accounts",
    "companies",
    "sales",
    "expenses",
    "reports.apps.ReportsConfig",
    "customers",
    "audit",
    "phone_refresh",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # WhiteNoise serves collected static assets straight from gunicorn so the
    # Coolify proxy does not need a separate static handler. Must sit right
    # after SecurityMiddleware (see https://whitenoise.readthedocs.io/).
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "core.middleware.SecurityHeadersMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    # Subdomain gate for the public phone-refresh page. Sits BEFORE
    # CommonMiddleware so its 404 / 302 responses bypass APPEND_SLASH
    # rewriting and language-prefix logic; sits AFTER SessionMiddleware
    # so a future evolution can branch on request.user without surprises.
    "phone_refresh.middleware.PhoneRefreshSubdomainMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# --- Security defaults (refined per environment) ---------------------------
# These can be overridden in development.py / production.py.
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SECURITY_HEADERS_ENABLED = True

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
                "core.context_processors.theme",
                "core.context_processors.site_branding",
                "core.context_processors.nav_notifications",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en"

LANGUAGES = [
    ("en", "English"),
    ("ar", "العربية"),
]

LOCALE_PATHS = [BASE_DIR / "locale"]

TIME_ZONE = "Asia/Jerusalem"

USE_I18N = True

USE_L10N = True

USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = Path(
    os.environ.get("DJANGO_STATIC_ROOT", str(BASE_DIR / "staticfiles"))
).resolve()

# Set by install.py on each deploy; templates append ?v=… to {% static %} CSS/JS URLs.
ASSET_CACHE_BUSTER = os.environ.get("DJANGO_ASSET_CACHE_BUSTER", "").strip()

MEDIA_URL = "media/"
MEDIA_ROOT = Path(os.environ.get("DJANGO_MEDIA_ROOT", str(BASE_DIR / "media"))).resolve()

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = reverse_lazy("accounts:login")
LOGIN_REDIRECT_URL = "core:home"
LOGOUT_REDIRECT_URL = reverse_lazy("accounts:login")
