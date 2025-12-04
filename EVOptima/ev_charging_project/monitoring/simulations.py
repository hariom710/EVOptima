"""
Simulation services for EV charging scenarios
"""
import time
import random
from dataclasses import dataclass
from typing import Optional
from .services import PredictedValues, check_fault, log_reading, log_event
from .models import Thresholds


@dataclass
class ChargingSimulation:
    """Base simulation class for EV charging scenarios"""
    start_time: float
    sample_period: float = 1.0
    running: bool = False
    
    def get_values(self) -> PredictedValues:
        """Generate simulated values - to be overridden by subclasses"""
        raise NotImplementedError


class NormalChargingSimulation(ChargingSimulation):
    """Simulates normal, safe charging operation"""
    
    def __init__(self, sample_period: float = 1.0):
        super().__init__(time.time(), sample_period)
        self.base_current = 20.0  # Safe middle range (10-30A)
        self.base_voltage = 430.0  # Safe middle range (400-460V)
        self.base_temperature = 40.0  # Safe middle range (0-80°C)
        
    def get_values(self) -> PredictedValues:
        """Generate normal charging values within safe thresholds"""
        elapsed = time.time() - self.start_time
        
        # Simulate gradual temperature increase during charging
        temp_increase = min(elapsed * 0.1, 15)  # Max 15°C increase
        current_temp = self.base_temperature + temp_increase + random.uniform(-3, 3)
        
        # Simulate slight variations in current and voltage
        current = self.base_current + random.uniform(-5, 5)
        voltage = self.base_voltage + random.uniform(-15, 15)
        
        # Ensure values stay within safe ranges
        current = max(12, min(current, 28))  # 12-28A (safe)
        voltage = max(410, min(voltage, 450))  # 410-450V (safe)
        current_temp = max(20, min(current_temp, 70))  # 20-70°C (safe)
        
        return PredictedValues(
            current=round(current, 2),
            voltage=round(voltage, 2),
            temperature=round(current_temp, 2)
        )


class FaultDetectionSimulation(ChargingSimulation):
    """Simulates various fault conditions for testing"""
    
    def __init__(self, sample_period: float = 1.0):
        super().__init__(time.time(), sample_period)
        self.fault_phase = 0  # 0=normal, 1=high_current, 2=low_voltage, 3=high_temp, 4=low_temp
        self.phase_start_time = time.time()
        self.phase_duration = 10.0  # 10 seconds per phase
        
    def get_values(self) -> PredictedValues:
        """Generate values that cycle through different fault conditions"""
        elapsed = time.time() - self.start_time
        phase_elapsed = time.time() - self.phase_start_time
        
        # Determine current phase
        phase_num = int(elapsed / self.phase_duration) % 5
        
        if phase_num != self.fault_phase:
            self.fault_phase = phase_num
            self.phase_start_time = time.time()
            phase_elapsed = 0
        
        # Phase 0: Normal charging (0-10s)
        if self.fault_phase == 0:
            current = 20.0 + random.uniform(-3, 3)
            voltage = 430.0 + random.uniform(-10, 10)
            temperature = 40.0 + random.uniform(-5, 5)
            
        # Phase 1: High Current fault (10-20s)
        elif self.fault_phase == 1:
            current = 35.0 + random.uniform(0, 10)  # 35-45A (exceeds 30A limit)
            voltage = 430.0 + random.uniform(-10, 10)
            temperature = 40.0 + random.uniform(-5, 5)
            
        # Phase 2: Low Voltage fault (20-30s)
        elif self.fault_phase == 2:
            current = 20.0 + random.uniform(-3, 3)
            voltage = 350.0 + random.uniform(0, 30)  # 350-380V (below 400V limit)
            temperature = 40.0 + random.uniform(-5, 5)
            
        # Phase 3: High Temperature fault (30-40s)
        elif self.fault_phase == 3:
            current = 20.0 + random.uniform(-3, 3)
            voltage = 430.0 + random.uniform(-10, 10)
            temperature = 85.0 + random.uniform(0, 15)  # 85-100°C (exceeds 80°C limit)
            
        # Phase 4: Low Temperature fault (40-50s)
        else:  # Phase 4
            current = 20.0 + random.uniform(-3, 3)
            voltage = 430.0 + random.uniform(-10, 10)
            temperature = -10.0 + random.uniform(0, 8)  # -10 to -2°C (below 0°C limit)
        
        return PredictedValues(
            current=round(current, 2),
            voltage=round(voltage, 2),
            temperature=round(temperature, 2)
        )


def run_simulation(simulation: ChargingSimulation, duration: Optional[float] = None, 
                   max_iterations: Optional[int] = None):
    """
    Run a charging simulation and log readings/faults
    
    Args:
        simulation: The simulation instance to run
        duration: Maximum duration in seconds (None for infinite)
        max_iterations: Maximum number of iterations (None for infinite)
    """
    simulation.running = True
    iterations = 0
    start_time = time.time()
    
    log_event('INFO', 'Simulation started', f'Type: {simulation.__class__.__name__}')
    
    try:
        while simulation.running:
            # Check duration limit
            if duration and (time.time() - start_time) >= duration:
                log_event('INFO', 'Simulation completed', 'Duration limit reached')
                break
            
            # Check iteration limit
            if max_iterations and iterations >= max_iterations:
                log_event('INFO', 'Simulation completed', f'Iteration limit reached: {max_iterations}')
                break
            
            # Get simulated values
            values = simulation.get_values()
            
            # Log the reading
            reading = log_reading(values)
            
            # Check for faults
            is_fault, fault_message = check_fault(values)
            
            if is_fault:
                log_event('FAULT_DETECTED', fault_message, 
                         f'Reading ID: {reading.id}, Current={values.current}A, Voltage={values.voltage}V, Temp={values.temperature}°C')
            
            iterations += 1
            time.sleep(simulation.sample_period)
            
    except KeyboardInterrupt:
        log_event('INFO', 'Simulation stopped', 'User interrupted')
    except Exception as e:
        log_event('INFO', 'Simulation error', f'Error: {str(e)}')
    finally:
        simulation.running = False
        log_event('INFO', 'Simulation ended', f'Total iterations: {iterations}')


