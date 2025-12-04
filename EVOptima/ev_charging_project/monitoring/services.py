from .models import Thresholds, Reading, EventLog
from django.db import transaction
from dataclasses import dataclass


@dataclass
class PredictedValues:
	current: float
	voltage: float
	temperature: float


def check_fault(predicted_values: PredictedValues) -> tuple[bool, str]:
	"""
	Check if predicted values violate safety thresholds.
	Returns (is_fault, fault_message)
	"""
	thresholds = Thresholds.objects.order_by('-updated_at').first()
	if not thresholds:
		thresholds = Thresholds.objects.create()
	
	faults = []
	
	# Check current
	if predicted_values.current > thresholds.max_current or predicted_values.current < thresholds.min_current:
		faults.append(f'Current: {predicted_values.current:.2f}A (limit: {thresholds.min_current}-{thresholds.max_current}A)')
	
	# Check voltage
	if predicted_values.voltage > thresholds.max_voltage or predicted_values.voltage < thresholds.min_voltage:
		faults.append(f'Voltage: {predicted_values.voltage:.2f}V (limit: {thresholds.min_voltage}-{thresholds.max_voltage}V)')
	
	# Check temperature
	if predicted_values.temperature > thresholds.max_temperature or predicted_values.temperature < thresholds.min_temperature:
		faults.append(f'Temperature: {predicted_values.temperature:.2f}°C (limit: {thresholds.min_temperature}-{thresholds.max_temperature}°C)')
	
	if faults:
		message = 'FAULT DETECTED - ' + '; '.join(faults)
		return True, message
	
	return False, "SAFE"


def log_reading(predicted_values: PredictedValues) -> Reading:
	"""Log a reading to the database"""
	return Reading.objects.create(
		current=predicted_values.current,
		voltage=predicted_values.voltage,
		temperature=predicted_values.temperature
	)


def log_event(event_type: str, details: str, response: str = ""):
	"""Log an event to the database"""
	return EventLog.objects.create(
		event_type=event_type,
		details=details,
		response=response
	)


