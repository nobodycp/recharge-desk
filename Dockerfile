# syntax=docker/dockerfile:1.4
# Playwright's official image ships Firefox + system deps preinstalled.
# Avoids ~200 MB download and ~90 s of apt work on every Coolify rebuild.
FROM mcr.microsoft.com/playwright/python:v1.60.0-noble AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=config.settings.production \
    PORT=8000

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq-dev \
        gettext \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && pip install -r requirements.txt

COPY . /app

RUN mkdir -p /app/media \
    && chmod +x /app/entrypoint.sh || true

EXPOSE 8000

CMD ["./entrypoint.sh"]
