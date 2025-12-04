"""
Management command to simulate normal EV charging
"""
from django.core.management.base import BaseCommand
from monitoring.simulations import NormalChargingSimulation, run_simulation


class Command(BaseCommand):
    help = 'Simulate normal EV charging operation with safe parameters'

    def add_arguments(self, parser):
        parser.add_argument(
            '--duration',
            type=float,
            default=None,
            help='Duration of simulation in seconds (default: infinite)'
        )
        parser.add_argument(
            '--iterations',
            type=int,
            default=None,
            help='Maximum number of iterations (default: infinite)'
        )
        parser.add_argument(
            '--period',
            type=float,
            default=1.0,
            help='Sample period in seconds (default: 1.0)'
        )

    def handle(self, *args, **options):
        duration = options['duration']
        max_iterations = options['iterations']
        sample_period = options['period']
        
        self.stdout.write(self.style.SUCCESS('Starting Normal Charging Simulation...'))
        self.stdout.write(f'Sample period: {sample_period}s')
        if duration:
            self.stdout.write(f'Duration: {duration}s')
        if max_iterations:
            self.stdout.write(f'Max iterations: {max_iterations}')
        self.stdout.write('Press Ctrl+C to stop\n')
        
        simulation = NormalChargingSimulation(sample_period=sample_period)
        
        try:
            run_simulation(simulation, duration=duration, max_iterations=max_iterations)
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING('\nSimulation stopped by user'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error: {e}'))
        
        self.stdout.write(self.style.SUCCESS('Simulation completed'))


