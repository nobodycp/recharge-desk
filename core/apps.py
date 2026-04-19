from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        # Wire up the cache-invalidation signals after the app registry
        # is fully loaded; importing earlier would race the model
        # imports below and Django would refuse with "Apps aren't
        # loaded yet".
        from core import signals  # noqa: F401
