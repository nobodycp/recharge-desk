FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=config.settings.production \
    PORT=8000

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
        gettext \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r requirements.txt \
    && playwright install firefox \
    && playwright install-deps firefox

COPY . /app

RUN mkdir -p /app/media \
    && chmod +x /app/entrypoint.sh || true

RUN DJANGO_SECRET_KEY=build-time-dummy \
    DJANGO_ALLOWED_HOSTS=build.local \
    DJANGO_CSRF_TRUSTED_ORIGINS=https://build.local \
    DATABASE_URL=sqlite:///tmp/build.sqlite3 \
    python manage.py collectstatic --noinput || true

EXPOSE 8000

CMD ["./entrypoint.sh"]
