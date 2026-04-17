#!/usr/bin/env bash
# Thin wrapper: ensures we run the installer with Python 3 from this repo tree.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${ROOT}/install.py" "$@"
