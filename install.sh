#!/usr/bin/env bash
# =============================================================================
# تشغيل مثبت السيرفر (ملف واحد من جذر المشروع)
# One-file launcher → install/install.py (Ubuntu 24 + PostgreSQL + Caddy + Gunicorn)
#
# الاستخدام / Usage:
#   chmod +x install.sh
#   sudo ./install.sh --config /path/to/your.install-config.json
#   sudo ./install.sh --config ... --dry-run
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_PY="${REPO_ROOT}/install/install.py"

if [[ ! -f "${INSTALL_PY}" ]]; then
	echo "ERROR: missing ${INSTALL_PY} (run this script from the repository root)." >&2
	exit 1
fi

exec python3 "${INSTALL_PY}" "$@"
