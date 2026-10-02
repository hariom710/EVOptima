"""Run charging simulations.

Two modes:

One-shot (replaces the old ``simulate_normal_charging`` and
``simulate_fault_detection`` commands, which were near-identical copies of each
other)::

    python manage.py run_sim --type normal --duration 60
    python manage.py run_sim --type fault --cycles 2 --period 0.5
    python manage.py run_sim --type fault --iterations 100

Daemon (the mode the web UI needs)::

    python manage.py run_sim --daemon

The daemon polls the ``SimulationControl`` row, so the *Start*/*Stop* buttons on
the visualization page work from any web worker and survive a web restart —
the module-level ``views._simulation_threads`` dict they used to depend on only
ever covered the single worker that handled the click. It refreshes
``heartbeat_at`` every tick, which is how the status endpoint distinguishes a
live simulator from a stale one, and clears ``active_type`` on any exit so a
Ctrl+C never leaves the UI claiming a simulation is still running.

One-shot mode deliberately does **not** write ``SimulationControl``: it is a
local CLI run, and claiming the shared control plane would fight with a daemon
that is following the web UI's requests.
"""
from __future__ import annotations

import time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.monitoring.models import SimulationControl
from apps.monitoring.services import (
    FaultDetectionSimulation,
    NormalChargingSimulation,
    log_event,
    run_sample,
    run_simulation,
)

#: Each fault cycle walks Normal -> High current -> Low voltage -> High temp
#: -> Low temp in 10 s phases (see FaultDetectionSimulation.phase_duration).
FAULT_CYCLE_SECONDS = 50.0

SIMULATIONS = {
    SimulationControl.SIM_NORMAL: NormalChargingSimulation,
    SimulationControl.SIM_FAULT: FaultDetectionSimulation,
}


class Command(BaseCommand):
    help = "Run charging simulations (one-shot, or --daemon to follow the web UI)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--type",
            choices=sorted(SIMULATIONS),
            default=None,
            help=(
                "Scenario to run. One-shot defaults to 'normal'; with --daemon it "
                "pre-seeds SimulationControl.requested_type (otherwise the daemon "
                "starts idle and waits for the web UI)"
            ),
        )
        parser.add_argument(
            "--daemon",
            action="store_true",
            help="Follow SimulationControl.requested_type so the web UI can start/stop simulations",
        )
        parser.add_argument(
            "--duration", type=float, default=None,
            help="Maximum duration in seconds (default: infinite)",
        )
        parser.add_argument(
            "--iterations", type=int, default=None,
            help="Maximum number of samples (default: infinite)",
        )
        parser.add_argument(
            "--period", type=float, default=1.0,
            help="Sample period in seconds (default: 1.0)",
        )
        parser.add_argument(
            "--cycles", type=int, default=None,
            help="Fault cycles to complete; sets --duration to cycles*50s (fault type only)",
        )

    # ------------------------------------------------------------------ util
    @staticmethod
    def _make(sim_type: str, period: float):
        return SIMULATIONS[sim_type](sample_period=period)

    def _resolve_duration(self, options) -> float | None:
        duration = options["duration"]
        cycles = options["cycles"]
        if duration is None and cycles:
            duration = cycles * FAULT_CYCLE_SECONDS
            self.stdout.write(f"Calculated duration: {duration}s for {cycles} cycle(s)")
        return duration

    @staticmethod
    def _release(control: SimulationControl) -> None:
        """Clear the running flags so the UI cannot claim a dead sim is live."""
        control.refresh_from_db()
        control.active_type = SimulationControl.SIM_NONE
        control.heartbeat_at = None
        control.save(update_fields=["active_type", "heartbeat_at", "updated_at"])

    # ----------------------------------------------------------------- modes
    def handle(self, *args, **options):
        if options["daemon"]:
            return self._daemon(options)
        return self._one_shot(options)

    def _one_shot(self, options) -> None:
        sim_type = options["type"] or SimulationControl.SIM_NORMAL
        period = options["period"]
        duration = self._resolve_duration(options)
        iterations = options["iterations"]

        self.stdout.write(self.style.SUCCESS(f"Starting {sim_type} simulation..."))
        self.stdout.write(f"Sample period: {period}s")
        if duration:
            self.stdout.write(f"Duration: {duration}s")
        if iterations:
            self.stdout.write(f"Max iterations: {iterations}")
        self.stdout.write("Press Ctrl+C to stop\n")

        simulation = self._make(sim_type, period)
        try:
            run_simulation(simulation, duration=duration, max_iterations=iterations)
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("\nSimulation stopped by user"))
        except Exception as exc:  # noqa: BLE001 - report, do not traceback
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS("Simulation completed"))

    def _daemon(self, options) -> None:
        period = options["period"]
        duration = options["duration"]
        max_iterations = options["iterations"]

        control = SimulationControl.get_solo()

        # Seed the request ONLY when --type was passed explicitly. `--type`
        # defaults to None precisely so a freshly booted daemon does not
        # override whatever the web UI last asked for.
        if options["type"] and not control.requested_type:
            control.requested_type = options["type"]
            control.save(update_fields=["requested_type", "updated_at"])

        self.stdout.write(self.style.SUCCESS("Simulator daemon started"))
        self.stdout.write(
            f"Following SimulationControl.requested_type (sample period {period}s, "
            f"stale heartbeat after {SimulationControl.HEARTBEAT_TIMEOUT}s)"
        )
        self.stdout.write("Press Ctrl+C to stop\n")

        active_type: str | None = None
        simulation = None
        started_at = time.monotonic()
        samples = 0

        # NOTE: deliberately no close_old_connections() here. The loop issues a
        # query every tick, so the connection is never idle and Django already
        # closes-and-reconnects on any error. Worse, with the default
        # CONN_MAX_AGE=0 it would expire the connection after each query and
        # close it out from under an open transaction.
        try:
            while True:
                # Re-read every tick: another worker may have written to it.
                control = SimulationControl.objects.get(pk=control.pk)
                desired = control.requested_type

                if desired != active_type:
                    if active_type:
                        self.stdout.write(f"Stopping {active_type} simulation")
                        log_event("INFO", "Simulation stopped", f"Type: {active_type}")
                    active_type = desired if desired in SIMULATIONS else None
                    simulation = self._make(active_type, period) if active_type else None
                    if active_type:
                        started_at = time.monotonic()
                        samples = 0
                        self.stdout.write(self.style.SUCCESS(f"Starting {active_type} simulation"))
                        log_event("INFO", "Simulation started", f"Type: {active_type}")

                limit_hit = False
                if simulation is not None and duration and (time.monotonic() - started_at) >= duration:
                    self.stdout.write("Duration limit reached")
                    limit_hit = True
                elif simulation is not None and max_iterations is not None and samples >= max_iterations:
                    self.stdout.write(f"Iteration limit reached: {max_iterations}")
                    limit_hit = True

                if limit_hit:
                    # Release the request too: otherwise the daemon would pick
                    # the same type straight back up on the next tick.
                    control.requested_type = SimulationControl.SIM_NONE
                    log_event("INFO", "Simulation completed", "Limit reached")
                    active_type = None
                    simulation = None

                if simulation is not None:
                    run_sample(simulation)
                    samples += 1

                control.active_type = active_type or ""
                control.heartbeat_at = timezone.now()
                control.save(update_fields=["requested_type", "active_type", "heartbeat_at", "updated_at"])
                time.sleep(period)

        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("\nDaemon stopped by user"))
        except Exception as exc:  # noqa: BLE001 - never leave a stale "running" flag
            log_event("INFO", "Simulation error", f"Daemon error: {exc}")
            raise CommandError(str(exc)) from exc
        finally:
            if active_type:
                log_event("INFO", "Simulation ended", f"Type: {active_type}")
            self._release(control)

        self.stdout.write(
            self.style.SUCCESS(f"Simulator daemon exited after {samples} sample(s)")
        )
