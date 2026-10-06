"""App registration for the scheduler.

No models and no migrations: the scheduler is pure arithmetic over a
demonstration fleet, and the page it renders writes nothing. Keeping it
model-free is deliberate -- the moment it grows a ``Booking`` table it needs
a data source that EVOptima does not have.
"""
from django.apps import AppConfig


class SchedulingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.scheduling"
    verbose_name = "Smart Charging Scheduler"
