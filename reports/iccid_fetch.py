"""Fill ``PhoneLineTracking.sim_identifier`` from provider ``number_info``."""
from __future__ import annotations

import logging

from django.db.models.functions import Lower, Trim

from providers.contracts import Capability
from providers.service import number_info, service_state
from reports.models import PhoneLineTracking
from reports.stale_phones import normalize_reference_key
from sales.models import Sale

log = logging.getLogger(__name__)

# Keep bulk statement actions inside a single request budget.
_PER_LINE_BUDGET_SECONDS = 20.0


def _last_sale_for_key(key: str) -> Sale | None:
    return (
        Sale.objects.exclude(status=Sale.Status.CANCELLED)
        .annotate(rk=Lower(Trim("reference_number")))
        .filter(rk=key)
        .select_related("company")
        .order_by("-created_at", "-pk")
        .first()
    )


def fill_sim_identifiers_from_provider(keys: list[str], *, user=None) -> dict:
    """Fetch ICCID for each key; never overwrite a non-empty ``sim_identifier``.

    Returns ``{"filled", "skipped", "failed"}`` counts.
    """
    filled = skipped = failed = 0
    user_id = getattr(user, "pk", None)
    seen: set[str] = set()

    for raw in keys:
        key = normalize_reference_key(raw)
        if not key or key in seen:
            continue
        seen.add(key)

        meta = PhoneLineTracking.objects.filter(reference_key=key).first()
        if meta and (meta.sim_identifier or "").strip():
            skipped += 1
            continue

        sale = _last_sale_for_key(key)
        if sale is None:
            failed += 1
            continue

        company = sale.company
        if service_state(company, Capability.NUMBER_INFO).code != "active":
            failed += 1
            continue

        provider_key = (company.provider_key or "").strip().lower()
        phone = (sale.reference_number or "").strip()
        try:
            result = number_info(
                provider_key,
                phone,
                _PER_LINE_BUDGET_SECONDS,
                source="management",
                user_id=user_id,
                sale_id=sale.pk,
            )
        except Exception:  # noqa: BLE001 — one bad line must not abort the batch
            log.exception("iccid fetch crashed for %s", phone)
            failed += 1
            continue

        iccid = ((result.info.iccid if result.ok and result.info else "") or "").strip()
        if not iccid:
            failed += 1
            continue

        PhoneLineTracking.objects.update_or_create(
            reference_key=key,
            defaults={
                "sim_identifier": iccid[:200],
                "last_display_reference": phone[:64],
            },
        )
        filled += 1

    return {"filled": filled, "skipped": skipped, "failed": failed}
