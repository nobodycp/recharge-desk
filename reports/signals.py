"""Signals for reports app."""

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from reports.models import PhoneLineTracking
from reports.stale_phones import normalize_reference_key
from sales.models import Sale


@receiver(post_save, sender=Sale)
def clear_manual_renew_on_new_sale(
    sender, instance: Sale, created: bool, **kwargs
) -> None:
    """A newly created real sale clears any virtual 1-day renew on that reference."""
    if not created or instance.status == Sale.Status.CANCELLED:
        return
    key = normalize_reference_key(instance.reference_number)
    if not key:
        return
    PhoneLineTracking.objects.filter(
        reference_key=key, manual_reset_at__isnull=False
    ).update(
        manual_reset_at=None,
        updated_at=timezone.now(),
    )


@receiver(post_save, sender=Sale)
def clear_statement_dismiss_on_new_sale(
    sender, instance: Sale, **kwargs
) -> None:
    """New non-cancelled activity puts a deleted number back on بيان الأرقام."""
    if instance.status == Sale.Status.CANCELLED:
        return
    key = normalize_reference_key(instance.reference_number)
    if not key:
        return
    PhoneLineTracking.objects.filter(reference_key=key, is_dismissed=True).update(
        is_dismissed=False,
        updated_at=timezone.now(),
    )
