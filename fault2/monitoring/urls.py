from django.urls import path
from . import views

urlpatterns = [
	path('', views.dashboard, name='dashboard'),
	path('status/', views.status, name='status'),
	path('thresholds/', views.thresholds_view, name='thresholds'),
	path('events/', views.events, name='events'),
] 