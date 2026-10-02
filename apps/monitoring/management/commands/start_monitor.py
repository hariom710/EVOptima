"""Start the real-time monitoring service.

Runs the sampling loop in a dedicated process so the web workers stay
stateless. Reads from a CSV replay source when ``--csv`` is given, otherwise
from the synthetic mock source.
"""
import time

from django.core.management.base import BaseCommand

from apps.monitoring.services import (
    CsvPredictionSource,
    MonitoringService,
    mock_prediction,
)


class Command(BaseCommand):
    help = "Start the real-time monitoring service (separate process)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv",
            type=str,
            default=None,
            help="Path to a CSV file to replay (defaults to settings.DATA_DIR dataset when omitted)",
        )
        parser.add_argument("--period", type=float, default=1.0, help="Sample period in seconds")
        parser.add_argument(
            "--fault-seconds",
            type=int,
            default=5,
            help="Consecutive samples required to declare a persistent fault",
        )
        parser.add_argument(
            "--stop-on-fault",
            action="store_true",
            help="Stop the loop as soon as a persistent fault is detected",
        )

    def handle(self, *args, **options):
        source = None
        if options["csv"]:
            source = CsvPredictionSource(options["csv"])
            get_pred = source.next
        else:
            get_pred = mock_prediction

        service = MonitoringService(
            get_prediction=get_pred,
            sample_period_sec=options["period"],
            fault_seconds_threshold=options["fault_seconds"],
            stop_on_fault=options["stop_on_fault"],
        )
        service.start()
        self.stdout.write(self.style.SUCCESS("Monitoring service started. Press Ctrl+C to stop."))
        try:
            while service.is_running():
                time.sleep(1)
        except KeyboardInterrupt:
            self.stdout.write("Stopping...")
        finally:
            service.stop()
            if source is not None:
                source.close()
            self.stdout.write(self.style.SUCCESS("Monitoring service stopped."))
