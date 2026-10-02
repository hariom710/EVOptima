from django.apps import AppConfig


class MonitoringConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.monitoring"
    verbose_name = "EV Monitoring"

    def ready(self):
        # Imported here, not at module level: receivers touch the models, and
        # apps.py is imported before the app registry is fully populated.
        from django.db.models.signals import post_delete, post_save

        from .models import Thresholds
        from .services.faults import invalidate_thresholds

        # Editing thresholds through the API or the admin must take effect on
        # the very next sample, not after the cache TTL.
        post_save.connect(
            invalidate_thresholds,
            sender=Thresholds,
            dispatch_uid="monitoring.thresholds.invalidate_on_save",
        )
        post_delete.connect(
            invalidate_thresholds,
            sender=Thresholds,
            dispatch_uid="monitoring.thresholds.invalidate_on_delete",
        )
