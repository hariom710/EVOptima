"""
Management command to simulate fault detection scenarios
"""
from django.core.management.base import BaseCommand
from monitoring.simulations import FaultDetectionSimulation, run_simulation


class Command(BaseCommand):
    help = 'Simulate various fault conditions for testing fault detection system'

    def add_arguments(self, parser):
        parser.add_argument(
            '--duration',
            type=float,
            default=None,
            help='Duration of simulation in seconds (default: infinite, cycles through all fault phases)'
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
        parser.add_argument(
            '--cycles',
            type=int,
            default=1,
            help='Number of fault cycles to complete (default: 1, each cycle = 50s covering all phases)'
        )

    def handle(self, *args, **options):
        duration = options['duration']
        max_iterations = options['iterations']
        sample_period = options['period']
        cycles = options['cycles']
        
        self.stdout.write(self.style.SUCCESS('Starting Fault Detection Simulation...'))
        self.stdout.write(f'Sample period: {sample_period}s')
        self.stdout.write(f'Fault cycles: {cycles} (each cycle = 50s: Normal→High Current→Low Voltage→High Temp→Low Temp)')
        if duration:
            self.stdout.write(f'Duration: {duration}s')
        if max_iterations:
            self.stdout.write(f'Max iterations: {max_iterations}')
        self.stdout.write('Press Ctrl+C to stop\n')
        
        simulation = FaultDetectionSimulation(sample_period=sample_period)
        
        # Calculate duration if cycles specified
        if not duration and cycles:
            cycle_duration = 50.0  # Each cycle is 50 seconds
            duration = cycles * cycle_duration
            self.stdout.write(f'Calculated duration: {duration}s for {cycles} cycles\n')
        
        try:
            run_simulation(simulation, duration=duration, max_iterations=max_iterations)
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING('\nSimulation stopped by user'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error: {e}'))
        
        self.stdout.write(self.style.SUCCESS('Fault detection simulation completed'))


