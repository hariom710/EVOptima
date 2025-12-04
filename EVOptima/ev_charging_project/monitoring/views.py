from django.shortcuts import render
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.utils import timezone
from django.forms.models import model_to_dict
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.contrib import messages
from django.shortcuts import redirect
from .models import Thresholds, Reading, EventLog
import threading
from .simulations import NormalChargingSimulation, FaultDetectionSimulation, run_simulation


@login_required
@api_view(['GET'])
def status(request):
	latest = Reading.objects.order_by('-created_at').first()
	thresholds = Thresholds.objects.order_by('-updated_at').first()
	state = 'SAFE'
	fault_type = None
	is_current_anomaly = False
	is_voltage_anomaly = False
	is_temperature_anomaly = False
	if latest and thresholds:
		if latest.current > thresholds.max_current or latest.current < thresholds.min_current:
			state = 'FAULT'
			fault_type = 'CURRENT_OUT_OF_RANGE'
			is_current_anomaly = True
		elif latest.voltage > thresholds.max_voltage or latest.voltage < thresholds.min_voltage:
			state = 'FAULT'
			fault_type = 'VOLTAGE_OUT_OF_RANGE'
			is_voltage_anomaly = True
		elif latest.temperature > thresholds.max_temperature or latest.temperature < thresholds.min_temperature:
			state = 'FAULT'
			fault_type = 'TEMPERATURE_OUT_OF_RANGE'
			is_temperature_anomaly = True
	return Response({
		'now': timezone.now().isoformat(),
		'latest_reading': model_to_dict(latest) if latest else None,
		'thresholds': model_to_dict(thresholds) if thresholds else None,
		'state': state,
		'fault_type': fault_type,
		'is_current_anomaly': is_current_anomaly,
		'is_voltage_anomaly': is_voltage_anomaly,
		'is_temperature_anomaly': is_temperature_anomaly,
	})


@login_required
@api_view(['GET', 'POST'])
def thresholds_view(request):
	if request.method == 'GET':
		th = Thresholds.objects.order_by('-updated_at').first()
		if not th:
			th = Thresholds.objects.create()
		return Response(model_to_dict(th))
	data = request.data
	th = Thresholds.objects.order_by('-updated_at').first() or Thresholds()
	th.min_current = float(data.get('min_current', th.min_current or 10))
	th.max_current = float(data.get('max_current', th.max_current or 30))
	th.min_voltage = float(data.get('min_voltage', th.min_voltage or 400))
	th.max_voltage = float(data.get('max_voltage', th.max_voltage or 460))
	th.min_temperature = float(data.get('min_temperature', th.min_temperature or 0))
	th.max_temperature = float(data.get('max_temperature', th.max_temperature or 80))
	th.save()
	EventLog.objects.create(event_type='THRESHOLDS_CHANGED', details='Thresholds updated', response='Using new thresholds')
	return Response(model_to_dict(th))


@login_required
@api_view(['GET'])
def events(request):
	items = EventLog.objects.order_by('-created_at')[:200]
	return Response([
		{
			'id': e.id,
			'event_type': e.event_type,
			'details': e.details,
			'response': e.response,
			'created_at': e.created_at.isoformat(),
		}
		for e in items
	])


# Global simulation thread storage
_simulation_threads = {}
_simulation_instances = {}


@login_required
@require_http_methods(["POST"])
def start_simulation(request):
	"""Start a simulation"""
	sim_type = request.POST.get('type', 'normal')
	duration = request.POST.get('duration')
	cycles = request.POST.get('cycles')
	max_iterations = request.POST.get('iterations')
	
	if sim_type in _simulation_threads and _simulation_threads[sim_type].is_alive():
		messages.warning(request, f'{sim_type} simulation is already running')
		# AJAX-friendly response
		if request.headers.get('x-requested-with') == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept', ''):
			return Response({'status': 'already_running', 'type': sim_type})
		return redirect('/visualization/')
	
	# Create simulation instance
	if sim_type == 'normal':
		simulation = NormalChargingSimulation()
	elif sim_type == 'fault':
		simulation = FaultDetectionSimulation()
	else:
		messages.error(request, 'Invalid simulation type')
		if request.headers.get('x-requested-with') == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept', ''):
			return Response({'status': 'error', 'message': 'invalid type'}, status=400)
		return redirect('/visualization/')
	
	# Parse duration and iterations
	duration_val = float(duration) if duration else None
	iterations_val = int(max_iterations) if max_iterations else None
	
	# For fault simulation, calculate duration from cycles if provided
	if sim_type == 'fault' and cycles and not duration_val:
		cycles_val = int(cycles)
		duration_val = cycles_val * 50.0  # Each cycle is 50 seconds
	
	# Start simulation in a separate thread
	def run_sim():
		run_simulation(simulation, duration=duration_val, max_iterations=iterations_val)
	
	thread = threading.Thread(target=run_sim, daemon=True)
	thread.start()
	
	_simulation_threads[sim_type] = thread
	_simulation_instances[sim_type] = simulation
	
	messages.success(request, f'{sim_type.capitalize()} simulation started')
	if request.headers.get('x-requested-with') == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept', ''):
		return Response({'status': 'started', 'type': sim_type})
	return redirect('/visualization/')


@login_required
@require_http_methods(["POST"])
def stop_simulation(request):
	"""Stop a running simulation"""
	sim_type = request.POST.get('type', 'normal')
	
	if sim_type in _simulation_instances:
		_simulation_instances[sim_type].running = False
		del _simulation_instances[sim_type]
		if sim_type in _simulation_threads:
			del _simulation_threads[sim_type]
		messages.success(request, f'{sim_type.capitalize()} simulation stopped')
	else:
		messages.warning(request, f'No {sim_type} simulation running')
	# AJAX-friendly response
	if request.headers.get('x-requested-with') == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept', ''):
		return Response({'status': 'stopped', 'type': sim_type})
	return redirect('/visualization/')

