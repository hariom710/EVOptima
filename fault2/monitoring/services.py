import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Iterator
from django.db import transaction
from .models import Thresholds, Reading, EventLog


@dataclass
class PredictedValues:
	current: float
	voltage: float
	temperature: float


class CsvPredictionSource:
	def __init__(self, csv_path: str, has_header: bool = True, current_idx: int = 4, voltage_idx: int = 3, temperature_idx: int = 5, delimiter: str = ','):
		self.csv_path = csv_path
		self.has_header = has_header
		self.current_idx = current_idx  # Charging Current_A is column 4
		self.voltage_idx = voltage_idx   # Charging Voltage_V is column 3
		self.temperature_idx = temperature_idx  # Battery Temperature_C is column 5
		self.delimiter = delimiter
		self._iter: Optional[Iterator[str]] = None

	def _ensure_iter(self):
		if self._iter is None:
			f = open('data/ev_charging_data.csv', 'r', encoding='utf-8')
			self._iter = iter(f)
			if self.has_header:
				try:
					next(self._iter)
				except StopIteration:
					pass

	def next(self) -> PredictedValues:
		self._ensure_iter()
		line = next(self._iter)
		parts = line.strip().split(self.delimiter)
		
		# Parse and validate values
		try:
			current = float(parts[self.current_idx])
			voltage = float(parts[self.voltage_idx])
			temperature = float(parts[self.temperature_idx])
			
			# Clamp values to realistic EV charger ranges
			current = max(0, min(current, 50))  # 0-50A range
			voltage = max(200, min(voltage, 300))  # 200-300V range  
			temperature = max(15, min(temperature, 80))  # 15-80°C range
			
			return PredictedValues(
				current=current,
				voltage=voltage,
				temperature=temperature,
			)
		except (ValueError, IndexError) as e:
			# If parsing fails, return safe default values
			return PredictedValues(
				current=20.0,
				voltage=230.0,
				temperature=25.0,
			)


import asyncio
from channels.layers import get_channel_layer

class MonitoringService:
	def __init__(self, get_prediction: Callable[[], PredictedValues], alert_func: Optional[Callable[[str, str], None]] = None, sample_period_sec: float = 1.0, fault_seconds_threshold: int = 5, stop_on_fault: bool = True, update_func: Optional[Callable[[dict], None]] = None):
		self.get_prediction = get_prediction
		self.alert_func = alert_func or self._default_alert
		self.sample_period_sec = sample_period_sec
		self.fault_seconds_threshold = fault_seconds_threshold
		self.stop_on_fault = stop_on_fault
		self.update_func = update_func or self._send_update
		self._stop_event = threading.Event()
		self._thread: Optional[threading.Thread] = None
		self._current_fault_counter = 0
		self._voltage_fault_counter = 0
		self._temperature_fault_counter = 0
		self._charging_enabled = True

	def start(self):
		if self._thread and self._thread.is_alive():
			return
		self._stop_event.clear()
		self._thread = threading.Thread(target=self._run_loop, name='MonitoringService', daemon=True)
		self._thread.start()

	def stop(self):
		self._stop_event.set()
		if self._thread:
			self._thread.join(timeout=5)

	def is_running(self) -> bool:
		return self._thread is not None and self._thread.is_alive()

	def _default_alert(self, subject: str, message: str):
		EventLog.objects.create(event_type='INFO', details=f"ALERT: {subject}", response=message)

	def _shutdown_charging(self, reason: str):
		self._charging_enabled = False
		EventLog.objects.create(event_type='CHARGING_STOPPED', details=reason, response='Charging stop signal issued')

	def _send_update(self, data):
		channel_layer = get_channel_layer()
		if channel_layer:
			asyncio.run(
				channel_layer.group_send(
					'monitoring',
					{
						'type': 'monitoring_update',
						'data': data
					}
				)
			)

	def _run_loop(self):
		while not self._stop_event.is_set():
			try:
				pred = self.get_prediction()
			except StopIteration:
				EventLog.objects.create(event_type='INFO', details='CSV exhausted', response='Monitoring stopped')
				self.update_func({'status': 'stopped', 'message': 'CSV exhausted'})
				break
			except Exception as e:
				EventLog.objects.create(event_type='INFO', details=f'Prediction error: {e}', response='Skipping cycle')
				self.update_func({'status': 'error', 'message': str(e)})
				time.sleep(self.sample_period_sec)
				continue

			with transaction.atomic():
				reading = Reading.objects.create(current=pred.current, voltage=pred.voltage, temperature=pred.temperature)
				thresholds = Thresholds.objects.order_by('-updated_at').select_for_update().first() or Thresholds.objects.create()

				current_fault = pred.current > thresholds.max_current or pred.current < thresholds.min_current
				voltage_fault = pred.voltage > thresholds.max_voltage or pred.voltage < thresholds.min_voltage
				temperature_fault = pred.temperature > thresholds.max_temperature or pred.temperature < thresholds.min_temperature

				self._current_fault_counter = self._current_fault_counter + 1 if current_fault else 0
				self._voltage_fault_counter = self._voltage_fault_counter + 1 if voltage_fault else 0
				self._temperature_fault_counter = self._temperature_fault_counter + 1 if temperature_fault else 0

				persistent_fault = (
					self._current_fault_counter >= self.fault_seconds_threshold or
					self._voltage_fault_counter >= self.fault_seconds_threshold or
					self._temperature_fault_counter >= self.fault_seconds_threshold
				)
				
				state = 'FAULT' if persistent_fault else 'SAFE'

				# Log when values return to normal after being abnormal
				if not current_fault and self._current_fault_counter == 0 and hasattr(self, '_was_current_fault'):
					EventLog.objects.create(
						event_type='INFO', 
						details=f'Current normalized: {pred.current}A (within limits: {thresholds.min_current}-{thresholds.max_current}A)',
						response='Current readings back to safe range'
					)
					delattr(self, '_was_current_fault')
				
				if not voltage_fault and self._voltage_fault_counter == 0 and hasattr(self, '_was_voltage_fault'):
					EventLog.objects.create(
						event_type='INFO', 
						details=f'Voltage normalized: {pred.voltage}V (within limits: {thresholds.min_voltage}-{thresholds.max_voltage}V)',
						response='Voltage readings back to safe range'
					)
					delattr(self, '_was_voltage_fault')
				
				if not temperature_fault and self._temperature_fault_counter == 0 and hasattr(self, '_was_temperature_fault'):
					EventLog.objects.create(
						event_type='INFO', 
						details=f'Temperature normalized: {pred.temperature}°C (within limit: ≤{thresholds.max_temperature}°C)',
						response='Temperature readings back to safe range'
					)
					delattr(self, '_was_temperature_fault')

				# Log individual abnormal readings even before persistent fault
				if current_fault:
					self._was_current_fault = True
					EventLog.objects.create(
						event_type='INFO', 
						details=f'Current abnormal: {pred.current}A (limits: {thresholds.min_current}-{thresholds.max_current}A)',
						response=f'Fault counter: {self._current_fault_counter}/{self.fault_seconds_threshold}'
					)
				if voltage_fault:
					self._was_voltage_fault = True
					EventLog.objects.create(
						event_type='INFO', 
						details=f'Voltage abnormal: {pred.voltage}V (limits: {thresholds.min_voltage}-{thresholds.max_voltage}V)',
						response=f'Fault counter: {self._voltage_fault_counter}/{self.fault_seconds_threshold}'
					)
				if temperature_fault:
					self._was_temperature_fault = True
					temp_status = "too high" if pred.temperature > thresholds.max_temperature else "too low"
					EventLog.objects.create(
						event_type='INFO', 
						details=f'Temperature abnormal: {pred.temperature}°C {temp_status} (limits: {thresholds.min_temperature}-{thresholds.max_temperature}°C)',
						response=f'Fault counter: {self._temperature_fault_counter}/{self.fault_seconds_threshold}'
					)

				if persistent_fault and self._charging_enabled:
					faults = []
					if self._current_fault_counter >= self.fault_seconds_threshold:
						faults.append(f'Current: {pred.current}A (limit: {thresholds.min_current}-{thresholds.max_current}A)')
					if self._voltage_fault_counter >= self.fault_seconds_threshold:
						faults.append(f'Voltage: {pred.voltage}V (limit: {thresholds.min_voltage}-{thresholds.max_voltage}V)')
					if self._temperature_fault_counter >= self.fault_seconds_threshold:
						temp_status = "too high" if pred.temperature > thresholds.max_temperature else "too low"
						faults.append(f'Temperature: {pred.temperature}°C {temp_status} (limits: {thresholds.min_temperature}-{thresholds.max_temperature}°C)')

					message = 'PERSISTENT FAULT - ' + '; '.join(faults)
					EventLog.objects.create(event_type='FAULT_DETECTED', details=message, response='Charging system protection activated')
					self._shutdown_charging(reason=message)
					self.alert_func('Persistent fault detected', message)
					if self.stop_on_fault:
						self._stop_event.set()
						
				from django.forms.models import model_to_dict
				self.update_func({
					'latest_reading': model_to_dict(reading),
					'state': state
				})

			time.sleep(self.sample_period_sec)


# Example prediction source for testing
import random

class ChargingSimulation:
	def __init__(self):
		self.start_time = time.time()
		self.phase = "normal"  # normal, fault_current_high, fault_voltage_low, fault_temp_high, fault_temp_low
		self.base_current = 20.0      # Safe middle range (10-30A)
		self.base_voltage = 430.0     # Safe middle range (400-460V)
		self.base_temperature = 40.0  # Safe middle range (0-80°C)
		self.logged_phases = set()    # Track which phases we've logged
		
	def get_prediction(self) -> PredictedValues:
		elapsed = time.time() - self.start_time
		
		# Phase 1: Normal charging (0-8 seconds)
		if elapsed < 8:
			if "normal" not in self.logged_phases:
				from .models import EventLog
				EventLog.objects.create(
					event_type='INFO', 
					details='Simulation started: Normal charging phase',
					response='Current: 10-30A, Voltage: 400-460V, Temperature: 0-80°C'
				)
				self.logged_phases.add("normal")
			self.phase = "normal"
			current = self.base_current + random.uniform(-3, 3)      # 17-23A (safe)
			voltage = self.base_voltage + random.uniform(-10, 10)    # 420-440V (safe)
			temperature = self.base_temperature + random.uniform(-5, 5)  # 35-45°C (safe)
			
		# Phase 2: High Current fault (8-15 seconds)
		elif elapsed < 15:
			if self.phase != "fault_current_high":
				from .models import EventLog
				EventLog.objects.create(
					event_type='INFO', 
					details=f'Simulation phase change: High current fault at {elapsed:.1f}s',
					response='Simulating excessive current draw (>30A)'
				)
				self.phase = "fault_current_high"
				print(f"[SIMULATION] Triggering high current fault at {elapsed:.1f}s")
			current = 35.0 + random.uniform(0, 10)  # 35-45A (exceeds 30A limit)
			voltage = self.base_voltage + random.uniform(-10, 10)
			temperature = self.base_temperature + random.uniform(-5, 5)
			
		# Phase 3: Low Voltage fault (15-25 seconds)
		elif elapsed < 25:
			if self.phase != "fault_voltage_low":
				from .models import EventLog
				EventLog.objects.create(
					event_type='INFO', 
					details=f'Simulation phase change: Low voltage fault at {elapsed:.1f}s',
					response='Simulating undervoltage condition (<400V)'
				)
				self.phase = "fault_voltage_low"
				print(f"[SIMULATION] Triggering low voltage fault at {elapsed:.1f}s")
			current = self.base_current + random.uniform(-3, 3)
			voltage = 350.0 + random.uniform(0, 30)  # 350-380V (below 400V limit)
			temperature = self.base_temperature + random.uniform(-5, 5)
			
		# Phase 4: High Temperature fault (25-35 seconds)
		elif elapsed < 35:
			if self.phase != "fault_temp_high":
				from .models import EventLog
				EventLog.objects.create(
					event_type='INFO', 
					details=f'Simulation phase change: High temperature fault at {elapsed:.1f}s',
					response='Simulating overheating condition (>80°C)'
				)
				self.phase = "fault_temp_high"
				print(f"[SIMULATION] Triggering high temperature fault at {elapsed:.1f}s")
			current = self.base_current + random.uniform(-3, 3)
			voltage = self.base_voltage + random.uniform(-10, 10)
			temperature = 85.0 + random.uniform(0, 15)  # 85-100°C (exceeds 80°C limit)
			
		# Phase 5: Low Temperature fault (35-45 seconds)
		elif elapsed < 45:
			if self.phase != "fault_temp_low":
				from .models import EventLog
				EventLog.objects.create(
					event_type='INFO', 
					details=f'Simulation phase change: Low temperature fault at {elapsed:.1f}s',
					response='Simulating freezing condition (<0°C)'
				)
				self.phase = "fault_temp_low"
				print(f"[SIMULATION] Triggering low temperature fault at {elapsed:.1f}s")
			current = self.base_current + random.uniform(-3, 3)
			voltage = self.base_voltage + random.uniform(-10, 10)
			temperature = -10.0 + random.uniform(0, 8)  # -10 to -2°C (below 0°C limit)
			
		# Phase 6: Recovery (45+ seconds)
		else:
			if self.phase != "recovery":
				from .models import EventLog
				EventLog.objects.create(
					event_type='INFO', 
					details=f'Simulation phase change: Recovery phase at {elapsed:.1f}s',
					response='All parameters returning to safe operating ranges'
				)
				self.phase = "recovery"
				print(f"[SIMULATION] Returning to normal operation at {elapsed:.1f}s")
			current = self.base_current + random.uniform(-3, 3)      # 17-23A (safe)
			voltage = self.base_voltage + random.uniform(-10, 10)    # 420-440V (safe)
			temperature = self.base_temperature + random.uniform(-5, 5)  # 35-45°C (safe)
		
		return PredictedValues(
			current=round(current, 2),
			voltage=round(voltage, 2),
			temperature=round(temperature, 2),
		)

# Create global simulation instance
charging_sim = ChargingSimulation()

def mock_prediction() -> PredictedValues:
	return charging_sim.get_prediction() 