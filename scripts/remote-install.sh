#!/bin/bash
# =============================================================================
# One-shot server install: clone your repo + merge minimal config + run installer.
# Run as root: curl ... | sudo bash -s -- <GIT_URL> <DOMAIN> [DB_PASSWORD]
#
# Environment (optional, overrides defaults):
#   RECHARGE_REPO, RECHARGE_DOMAIN, RECHARGE_DB_PASSWORD
#   RECHARGE_INSTALL_DIR (default /opt/recharge-desk)
#   RECHARGE_CONFIG        (default /root/recharge.install-config.json)
#
# If RECHARGE_DB_PASSWORD is empty and no 3rd argument: a password is generated
# and written to /root/recharge-desk.generated-db-password.txt (chmod 600).
# =============================================================================
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
	echo "ERROR: run as root. Example: curl -fsSL ... | sudo bash -s -- 'https://...git' 'app.example.com'" >&2
	exit 1
fi

REPO="${RECHARGE_REPO:-${1:-}}"
DOMAIN="${RECHARGE_DOMAIN:-${2:-}}"
DBPW="${RECHARGE_DB_PASSWORD:-${3:-}}"
INSTALL_DIR="${RECHARGE_INSTALL_DIR:-/opt/recharge-desk}"
CONFIG="${RECHARGE_CONFIG:-/root/recharge.install-config.json}"
EXAMPLE="${INSTALL_DIR}/install/config.example.json"

if [[ -z "${REPO}" ]]; then
	cat >&2 <<'EOF'
ERROR: missing git repository URL.

Usage:
  curl -fsSL https://raw.githubusercontent.com/OWNER/REPO/main/scripts/remote-install.sh | sudo bash -s -- \
    'https://github.com/OWNER/REPO.git' \
    'app.example.com' \
    'optional-database-password'

Or set RECHARGE_REPO and RECHARGE_DOMAIN in the environment before piping to sudo.
If you omit the password, one will be generated and saved under /root/recharge-desk.generated-db-password.txt
EOF
	exit 1
fi

if [[ -z "${DOMAIN}" ]]; then
	echo "ERROR: missing domain (hostname for HTTPS), e.g. app.example.com" >&2
	exit 1
fi

if [[ -z "${DBPW}" ]]; then
	DBPW="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
	echo "[remote-install] Generated PostgreSQL app password -> /root/recharge-desk.generated-db-password.txt"
	umask 077
	printf '%s\n' "${DBPW}" > /root/recharge-desk.generated-db-password.txt
	chmod 600 /root/recharge-desk.generated-db-password.txt
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y git python3

if [[ -d "${INSTALL_DIR}/.git" ]]; then
	echo "[remote-install] Updating existing clone at ${INSTALL_DIR}"
	git -C "${INSTALL_DIR}" remote set-url origin "${REPO}"
	git -C "${INSTALL_DIR}" fetch --depth 1 origin
	cur="$(git -C "${INSTALL_DIR}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
	if git -C "${INSTALL_DIR}" show-ref --verify --quiet "refs/remotes/origin/${cur}"; then
		git -C "${INSTALL_DIR}" reset --hard -q "origin/${cur}"
	else
		for fallback in main master; do
			if git -C "${INSTALL_DIR}" show-ref --verify --quiet "refs/remotes/origin/${fallback}"; then
				git -C "${INSTALL_DIR}" checkout -q "${fallback}"
				git -C "${INSTALL_DIR}" reset --hard -q "origin/${fallback}"
				break
			fi
		done
	fi
else
	echo "[remote-install] Cloning ${REPO} -> ${INSTALL_DIR}"
	rm -rf "${INSTALL_DIR}"
	git clone --depth 1 "${REPO}" "${INSTALL_DIR}"
fi

if [[ ! -f "${EXAMPLE}" ]]; then
	echo "ERROR: cloned tree missing ${EXAMPLE} (wrong repository?)." >&2
	exit 1
fi

INSTALL_PY="${INSTALL_DIR}/install/install.py"
if [[ ! -f "${INSTALL_PY}" ]]; then
	echo "ERROR: missing ${INSTALL_PY}" >&2
	exit 1
fi

if [[ -f "${CONFIG}" ]]; then
	echo "[remote-install] Merging domain/password into existing ${CONFIG}"
else
	echo "[remote-install] Creating ${CONFIG} from template"
	cp "${EXAMPLE}" "${CONFIG}"
	chmod 600 "${CONFIG}"
fi

export RECHARGE_CONFIG="${CONFIG}"
export RECHARGE_DOMAIN="${DOMAIN}"
export RECHARGE_DB_PASSWORD="${DBPW}"
python3 - <<'PY'
import json, os

path = os.environ["RECHARGE_CONFIG"]
domain = os.environ["RECHARGE_DOMAIN"]
password = os.environ["RECHARGE_DB_PASSWORD"]

with open(path, encoding="utf-8") as f:
    cfg = json.load(f)

cfg["domain"] = domain
cfg["django"]["allowed_hosts"] = [domain]
cfg["django"]["csrf_trusted_origins"] = [f"https://{domain}"]
cfg["postgres"]["password"] = password

with open(path, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
PY

echo "[remote-install] Starting installer (Python, not shell wrapper)..."
exec python3 "${INSTALL_PY}" --config "${CONFIG}"
