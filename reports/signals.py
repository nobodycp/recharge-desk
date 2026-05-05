"""Signals for reports app."""

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from reports.models import PhoneLineTracking
from reports.stale_phones import normalize_reference_key
from sales.models import Sale


@receiver(post_save, sender=Sale)
def clear_stale_report_dismiss_on_new_sale(sender, instance: Sale, **kwargs) -> None:
    """New non-cancelled activity on a reference re-enables it for the idle-lines report."""
    if instance.status == Sale.Status.CANCELLED:
        return
    key = normalize_reference_key(instance.reference_number)
    if not key:
        return
    PhoneLineTracking.objects.filter(reference_key=key, is_dismissed=True).update(
        is_dismissed=False,
        updated_at=timezone.now(),
    )
