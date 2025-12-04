from django.urls import path
from . import views

urlpatterns = [
	path('status/', views.status, name='monitoring_status'),
	path('thresholds/', views.thresholds_view, name='monitoring_thresholds'),
	path('events/', views.events, name='monitoring_events'),
	path('simulate/start/', views.start_simulation, name='start_simulation'),
	path('simulate/stop/', views.stop_simulation, name='stop_simulation'),
]

