from django.shortcuts import render
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.utils import timezone
from django.forms.models import model_to_dict
from .models import Thresholds, Reading, EventLog


# Create your views here.


@api_view(['GET'])
def status(request):
	latest = Reading.objects.order_by('-created_at').first()
	thresholds = Thresholds.objects.order_by('-updated_at').first()
	state = 'SAFE'
	if latest and thresholds:
		if latest.current > thresholds.max_current or latest.current < thresholds.min_current:
			state = 'FAULT'
		elif latest.voltage > thresholds.max_voltage or latest.voltage < thresholds.min_voltage:
			state = 'FAULT'
		elif latest.temperature > thresholds.max_temperature:
			state = 'FAULT'
	return Response({
		'now': timezone.now().isoformat(),
		'latest_reading': model_to_dict(latest) if latest else None,
		'thresholds': model_to_dict(thresholds) if thresholds else None,
		'state': state,
	})


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


@api_view(['GET'])
def dashboard(request):
	return render(request, 'dashboard.html')
