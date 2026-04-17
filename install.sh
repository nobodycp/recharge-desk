#!/bin/bash
# =============================================================================
# Server installer launcher (Ubuntu 24). Always runs Python explicitly — no
# reliance on the executable bit or CRLF-safe shebang on install.sh itself.
#
# Usage:
#   sudo bash install.sh --config /path/to/config.json
#   sudo bash install.sh
#       → uses ./install.config.json in this repo root if that file exists
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_PY="${REPO_ROOT}/install/install.py"

if [[ ! -f "${INSTALL_PY}" ]]; then
	echo "ERROR: missing ${INSTALL_PY} (run from repository root)." >&2
	exit 1
fi

CONFIG=""
ARGS=()
while [[ $# -gt 0 ]]; do
	case "$1" in
		--config)
			if [[ $# -lt 2 ]]; then
				echo "ERROR: --config requires a path." >&2
				exit 1
			fi
			CONFIG="$2"
			shift 2
			;;
		--config=*)
			CONFIG="${1#*=}"
			shift
			;;
		*)
			ARGS+=("$1")
			shift
			;;
	esac
done

if [[ -z "${CONFIG}" ]]; then
	if [[ -f "${REPO_ROOT}/install.config.json" ]]; then
		CONFIG="${REPO_ROOT}/install.config.json"
	fi
fi

if [[ -z "${CONFIG}" ]]; then
	cat >&2 <<EOF
ERROR: No config file.

Option A — config next to the repo (recommended):
  sudo cp ${REPO_ROOT}/install/config.example.json ${REPO_ROOT}/install.config.json
  sudo nano ${REPO_ROOT}/install.config.json
     Change: domain, postgres.password, django.allowed_hosts, django.csrf_trusted_origins
  sudo chmod 600 ${REPO_ROOT}/install.config.json
  sudo bash ${REPO_ROOT}/install.sh

Option B — explicit path:
  sudo bash ${REPO_ROOT}/install.sh --config /root/recharge.install-config.json
EOF
	exit 1
fi

exec python3 "${INSTALL_PY}" --config "${CONFIG}" "${ARGS[@]}"
