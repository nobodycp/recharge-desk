#!/usr/bin/env bash
# =============================================================================
# تحديث نسخة المشروع محلياً (واختيارياً على السيرفر عبر SSH).
# Local: git pull + .venv pip + migrate
# Server: set DEPLOY_SSH then run (see bottom of this file).
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${REPO_ROOT}"

echo "==> git pull --ff-only"
git pull --ff-only "$@"

if [[ -d "${REPO_ROOT}/.venv" ]]; then
	echo "==> pip install -r requirements.txt"
	"${REPO_ROOT}/.venv/bin/pip" install -q -r "${REPO_ROOT}/requirements.txt"
	echo "==> migrate"
	"${REPO_ROOT}/.venv/bin/python" "${REPO_ROOT}/manage.py" migrate --noinput
	echo "Local update done. Run: source .venv/bin/activate && python manage.py runserver"
else
	echo "No .venv — create once: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
fi

if [[ -n "${DEPLOY_SSH:-}" ]]; then
	echo "==> Remote pull + restart (DEPLOY_SSH=${DEPLOY_SSH})"
	# Adjust INSTALL_DIR on the server if yours is not /opt/recharge-desk
	REMOTE_INSTALL="${DEPLOY_INSTALL_DIR:-/opt/recharge-desk}"
	ssh "${DEPLOY_SSH}" "sudo git -c safe.directory=${REMOTE_INSTALL} -C ${REMOTE_INSTALL} pull --ff-only && sudo systemctl restart recharge-desk.service"
	echo "Server updated."
fi
