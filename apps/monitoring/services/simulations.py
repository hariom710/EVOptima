"""Synthetic charging scenarios used by the simulation management commands.

Two scenarios are provided:

- :class:`NormalChargingSimulation` — stays inside the safe band.
- :class:`FaultDetectionSimulation` — cycles through every fault phase
  (high current, low voltage, high temperature, low temperature).
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from .events import log_event, log_reading
from .faults import check_fault
from .values import PredictedValues


@dataclass
class ChargingSimulation:
    """Base simulation class for EV charging scenarios."""

    start_time: float = field(default_factory=time.time)
    sample_period: float = 1.0
    running: bool = False

    def get_values(self) -> PredictedValues:
        """Generate simulated values — to be overridden by subclasses."""
        raise NotImplementedError


@dataclass
class NormalChargingSimulation(ChargingSimulation):
    """Simulates normal, safe charging operation."""

    base_current: float = 20.0      # safe middle of 10-30 A
    base_voltage: float = 430.0     # safe middle of 400-460 V
    base_temperature: float = 40.0  # safe middle of 0-80 degC

    def get_values(self) -> PredictedValues:
        elapsed = time.time() - self.start_time

        # Gradual temperature increase while charging (capped at +15 degC).
        temp_increase = min(elapsed * 0.1, 15)
        current_temp = self.base_temperature + temp_increase + random.uniform(-3, 3)

        current = self.base_current + random.uniform(-5, 5)
        voltage = self.base_voltage + random.uniform(-15, 15)

        # Keep the sample inside the safe band.
        current = max(12, min(current, 28))
        voltage = max(410, min(voltage, 450))
        current_temp = max(20, min(current_temp, 70))

        return PredictedValues(
            current=round(current, 2),
            voltage=round(voltage, 2),
            temperature=round(current_temp, 2),
        )


@dataclass
class FaultDetectionSimulation(ChargingSimulation):
    """Cycles through every fault phase: normal, high I, low V, high T, low T."""

    fault_phase: int = 0
    phase_duration: float = 10.0
    phase_start_time: float = field(default_factory=time.time)

    def get_values(self) -> PredictedValues:
        elapsed = time.time() - self.start_time

        phase_num = int(elapsed / self.phase_duration) % 5
        if phase_num != self.fault_phase:
            self.fault_phase = phase_num
            self.phase_start_time = time.time()

        if self.fault_phase == 0:          # normal
            current = 20.0 + random.uniform(-3, 3)
            voltage = 430.0 + random.uniform(-10, 10)
            temperature = 40.0 + random.uniform(-5, 5)
        elif self.fault_phase == 1:        # high current (> 30 A)
            current = 35.0 + random.uniform(0, 10)
            voltage = 430.0 + random.uniform(-10, 10)
            temperature = 40.0 + random.uniform(-5, 5)
        elif self.fault_phase == 2:        # low voltage (< 400 V)
            current = 20.0 + random.uniform(-3, 3)
            voltage = 350.0 + random.uniform(0, 30)
            temperature = 40.0 + random.uniform(-5, 5)
        elif self.fault_phase == 3:        # high temperature (> 80 degC)
            current = 20.0 + random.uniform(-3, 3)
            voltage = 430.0 + random.uniform(-10, 10)
            temperature = 85.0 + random.uniform(0, 15)
        else:                              # low temperature (< 0 degC)
            current = 20.0 + random.uniform(-3, 3)
            voltage = 430.0 + random.uniform(-10, 10)
            temperature = -10.0 + random.uniform(0, 8)

        return PredictedValues(
            current=round(current, 2),
            voltage=round(voltage, 2),
            temperature=round(temperature, 2),
        )


def run_sample(simulation: ChargingSimulation) -> tuple[PredictedValues, object, bool, str]:
    """Take exactly one sample: persist it, evaluate it, log any fault.

    Split out of :func:`run_simulation` so the ``run_sim`` daemon can drive the
    loop one sample at a time while still servicing start/stop requests from
    the web tier.

    Returns:
        ``(values, reading, is_fault, fault_message)``
    """
    values = simulation.get_values()
    reading = log_reading(values)
    is_fault, fault_message = check_fault(values)

    if is_fault:
        log_event(
            "FAULT_DETECTED",
            fault_message,
            f"Reading ID: {reading.id}, Current={values.current}A, "
            f"Voltage={values.voltage}V, Temp={values.temperature}\u00b0C",
        )
    return values, reading, is_fault, fault_message


def run_simulation(
    simulation: ChargingSimulation,
    duration: float | None = None,
    max_iterations: int | None = None,
) -> int:
    """Run a simulation, logging readings and faults.

    Args:
        simulation: The simulation instance to run.
        duration: Maximum duration in seconds (``None`` = unlimited).
        max_iterations: Maximum number of samples (``None`` = unlimited).

    Returns:
        The number of samples written.
    """
    simulation.running = True
    iterations = 0
    start_time = time.time()

    log_event("INFO", "Simulation started", f"Type: {simulation.__class__.__name__}")

    try:
        while simulation.running:
            if duration and (time.time() - start_time) >= duration:
                log_event("INFO", "Simulation completed", "Duration limit reached")
                break
            if max_iterations and iterations >= max_iterations:
                log_event("INFO", "Simulation completed", f"Iteration limit reached: {max_iterations}")
                break

            run_sample(simulation)

            iterations += 1
            time.sleep(simulation.sample_period)

    except KeyboardInterrupt:
        log_event("INFO", "Simulation stopped", "User interrupted")
    except Exception as exc:  # noqa: BLE001 - keep the loop alive, report and exit
        log_event("INFO", "Simulation error", f"Error: {exc}")
    finally:
        simulation.running = False
        log_event("INFO", "Simulation ended", f"Total iterations: {iterations}")

    return iterations
