"""Shared env paths for sky_lab CLI scripts."""

from __future__ import annotations

import os
import sys
from pathlib import Path

LAB_DIR = Path(__file__).resolve().parent
REPO_ROOT = LAB_DIR.parent

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("SKY_SALES_ENV_FILE", str(LAB_DIR / ".env"))
os.environ.setdefault("SKY_SALES_SESSION_FILE", str(LAB_DIR / ".sky_session.json"))
