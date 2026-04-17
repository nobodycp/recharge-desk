from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from accounts.models import UserProfile

User = get_user_model()


@receiver(post_save, sender=User)
def ensure_profile(sender, instance, created, **kwargs):
    if created:
        role = (
            UserProfile.Role.MANAGEMENT
            if instance.is_superuser
            else UserProfile.Role.EMPLOYEE
        )
        UserProfile.objects.get_or_create(
            user=instance,
            defaults={
                "full_name": instance.get_full_name() or instance.username,
                "role": role,
            },
        )
