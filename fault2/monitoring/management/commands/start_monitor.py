from django.core.management.base import BaseCommand
from monitoring.services import MonitoringService, mock_prediction, CsvPredictionSource


class Command(BaseCommand):
	help = 'Start real-time monitoring service'

	def add_arguments(self, parser):
		parser.add_argument('--csv', type=str, help='Path to CSV file to stream predictions from')
		parser.add_argument('--period', type=float, default=1.0, help='Sample period seconds')
		parser.add_argument('--fault-seconds', type=int, default=5, help='Consecutive seconds to treat as persistent fault')

	def handle(self, *args, **options):
		csv_path = options.get('csv')
		period = options.get('period')
		fault_seconds = options.get('fault_seconds')

		if csv_path:
			source = CsvPredictionSource(csv_path)
			def get_pred():
				return source.next()
		else:
			get_pred = mock_prediction

		service = MonitoringService(get_prediction=get_pred, sample_period_sec=period, fault_seconds_threshold=fault_seconds, stop_on_fault=False)
		service.start()
		self.stdout.write(self.style.SUCCESS('Monitoring service started. Press Ctrl+C to stop.'))
		try:
			while service.is_running():
				from time import sleep
				sleep(1)
		except KeyboardInterrupt:
			self.stdout.write('Stopping...')
		finally:
			service.stop()
			self.stdout.write(self.style.SUCCESS('Monitoring service stopped.')) 