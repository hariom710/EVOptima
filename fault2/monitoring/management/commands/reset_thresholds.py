from django.core.management.base import BaseCommand
from monitoring.models import Thresholds, EventLog


class Command(BaseCommand):
    help = 'Reset thresholds to correct EV charger values'

    def handle(self, *args, **options):
        # Delete existing thresholds
        Thresholds.objects.all().delete()
        
        # Create new thresholds with correct values
        thresholds = Thresholds.objects.create(
            min_current=10.0,
            max_current=30.0,
            min_voltage=400.0,
            max_voltage=460.0,
            min_temperature=0.0,
            max_temperature=80.0
        )
        
        # Log the threshold reset
        EventLog.objects.create(
            event_type='THRESHOLDS_CHANGED',
            details='Thresholds reset to EV charger standards',
            response=f'Current: 10-30A, Voltage: 400-460V, Temperature: 0-80°C'
        )
        
        self.stdout.write(
            self.style.SUCCESS(f'Successfully reset thresholds: {thresholds}')
        )
