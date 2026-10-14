"""Container health check for the ``web`` service: exit 0 when ``/healthz/`` answers 200.

The image ships neither curl nor wget, and Django rejects ``Host: localhost``
(not in ``DJANGO_ALLOWED_HOSTS``), so the request carries the first allowed
host instead. Stdlib only: runs without loading Django.

Coolify: Health check type ``cmd``, command ``python /app/scripts/healthcheck.py``.
"""
import os
import sys
import urllib.request


def host_header() -> str:
    first = (os.environ.get("DJANGO_ALLOWED_HOSTS") or "").split(",")[0].strip()
    first = first.lstrip(".")
    return first if first and first != "*" else "localhost"


def check() -> int:
    port = os.environ.get("PORT", "8000")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/healthz/", headers={"Host": host_header()}
    )
    try:
        with urllib.request.urlopen(request, timeout=4) as response:
            return 0 if response.status == 200 else 1
    except Exception:  # noqa: BLE001 — any failure means "not healthy"
        return 1


if __name__ == "__main__":
    sys.exit(check())
