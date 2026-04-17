#!/usr/bin/env bash
# يسحب آخر التغييرات من GitHub (نفس فرع الـ upstream المربوط محلياً).
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
git pull --ff-only "$@"
