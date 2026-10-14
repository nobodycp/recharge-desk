"""Phone-number normalisation shared by every provider."""
from __future__ import annotations

import re


def norm_phone(raw: str) -> str:
    d = re.sub(r"\D", "", str(raw or "").strip())
    if not d:
        return ""
    if d.startswith("972"):
        d = "0" + d[3:]
    if len(d) == 9 and d[0] == "5":
        d = "0" + d
    return d
