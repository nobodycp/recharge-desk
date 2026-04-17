#!/usr/bin/env bash
# =============================================================================
# Remote one-shot: clone Recharge Desk + run install/install.py with a filled
# config. Intended to be run as root, typically via:
#   curl -fsSL .../remote-install.sh | sudo -E bash -s
# Export before sudo -E:
#   RECHARGE_DB_PASSWORD  (required)
#   RECHARGE_DOMAIN       (optional, default s.prosim.ps)
#   RECHARGE_REPO         (optional, git clone URL)
#   RECHARGE_INSTALL_DIR  (optional, default /opt/recharge-desk)
#   RECHARGE_CONFIG       (optional, default /root/recharge.install-config.json)
# =============================================================================
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
	echo "ERROR: run as root (use: curl ... | sudo -E bash -s)" >&2
	exit 1
fi

if [[ -z "${RECHARGE_DB_PASSWORD:-}" ]]; then
	echo "ERROR: set RECHARGE_DB_PASSWORD (PostgreSQL app user password)." >&2
	echo "Example:  RECHARGE_DB_PASSWORD='...' RECHARGE_DOMAIN='s.prosim.ps' curl -fsSL ... | sudo -E bash -s" >&2
	exit 1
fi

RECHARGE_DOMAIN="${RECHARGE_DOMAIN:-s.prosim.ps}"
RECHARGE_REPO="${RECHARGE_REPO:-https://github.com/nobodycp/recharge-desk.git}"
INSTALL_DIR="${RECHARGE_INSTALL_DIR:-/opt/recharge-desk}"
CONFIG="${RECHARGE_CONFIG:-/root/recharge.install-config.json}"

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y git python3

if [[ -d "${INSTALL_DIR}/.git" ]]; then
	echo "[remote-install] Updating existing clone at ${INSTALL_DIR}"
	git -C "${INSTALL_DIR}" fetch --depth 1 origin main
	git -C "${INSTALL_DIR}" checkout -q main
	git -C "${INSTALL_DIR}" reset --hard -q "origin/main" || git -C "${INSTALL_DIR}" pull --ff-only -q
else
	echo "[remote-install] Cloning into ${INSTALL_DIR}"
	rm -rf "${INSTALL_DIR}"
	git clone --depth 1 --branch main "${RECHARGE_REPO}" "${INSTALL_DIR}"
fi

cp "${INSTALL_DIR}/install/config.example.json" "${CONFIG}"
chmod 600 "${CONFIG}"

export RECHARGE_CONFIG="${CONFIG}"
export RECHARGE_DOMAIN
export RECHARGE_DB_PASSWORD
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

chmod +x "${INSTALL_DIR}/install.sh"
exec "${INSTALL_DIR}/install.sh" --config "${CONFIG}"
