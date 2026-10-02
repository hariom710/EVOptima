"""Reset thresholds to the EV-charger safety defaults."""
from django.core.management.base import BaseCommand

from apps.monitoring.models import EventLog, Thresholds


class Command(BaseCommand):
    help = "Reset thresholds to the standard EV charger safety limits"

    def handle(self, *args, **options):
        Thresholds.objects.all().delete()

        thresholds = Thresholds.objects.create(
            min_current=10.0,
            max_current=30.0,
            min_voltage=400.0,
            max_voltage=460.0,
            min_temperature=0.0,
            max_temperature=80.0,
        )

        EventLog.objects.create(
            event_type="THRESHOLDS_CHANGED",
            details="Thresholds reset to EV charger standards",
            response="Current: 10-30A, Voltage: 400-460V, Temperature: 0-80\u00b0C",
        )

        self.stdout.write(self.style.SUCCESS(f"Successfully reset thresholds: {thresholds}"))
