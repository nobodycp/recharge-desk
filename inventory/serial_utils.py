"""Parse optional serial/ICCID lists from management forms."""

from __future__ import annotations


def parse_serial_list(text: str) -> list[str]:
    """One serial per line (commas also accepted). Empty input → []."""
    if not (text or "").strip():
        return []
    serials: list[str] = []
    for raw in text.replace(",", "\n").split("\n"):
        value = raw.strip()
        if value:
            serials.append(value)
    return serials


def validate_serial_count(*, serials: list[str], qty: int) -> None:
    if not serials:
        return
    if len(serials) != qty:
        from django.utils.translation import gettext_lazy as _

        raise ValueError(
            _("Provide exactly %(qty)s serial number(s), one per line.")
            % {"qty": qty}
        )
    if len(set(s.lower() for s in serials)) != len(serials):
        from django.utils.translation import gettext_lazy as _

        raise ValueError(_("Duplicate serial numbers in the list."))
