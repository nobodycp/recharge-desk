"""Legacy company-name → provider heuristic, used only by ``providers_mapping_report``.

Routing never uses this; the explicit ``Company.phone_refresh_provider`` field is
the only source. The rules mirror the inline copy in migration
``companies/0012_fill_provider_from_name`` (which stays self-contained).
"""
from __future__ import annotations

# Same order as the old COMPANY_TO_PROVIDER dict (first match wins).
NAME_RULES = (
    ("sky", "sky"),
    ("aloha", "aloha"),
    ("layan", "layan"),
    ("areen", "areen"),
)
SKY_ARABIC = "سكاي"


def provider_for_name(name: str | None) -> str:
    """Provider key the old name rules would pick, or ``""`` for no match."""
    lowered = (name or "").strip().lower()
    for needle, provider_key in NAME_RULES:
        if needle in lowered:
            return provider_key
    if SKY_ARABIC in (name or ""):
        return "sky"
    return ""
