"""Monitoring API routes (canonical namespace ``monitoring``).

Included **once** at ``/api/monitoring/``. The fault2 short aliases live in
``api_urls.py`` and the dashboard page in ``dashboard_urls.py`` so that no
namespace is registered twice (Django check ``urls.W005``).

Reversed as ``monitoring:status``, ``monitoring:thresholds``, ...
"""
from django.urls import path

from . import views

app_name = "monitoring"

urlpatterns = [
    path("status/", views.status, name="status"),
    path("thresholds/", views.thresholds_view, name="thresholds"),
    path("events/", views.events, name="events"),
    path("simulate/start/", views.start_simulation, name="start_simulation"),
    path("simulate/stop/", views.stop_simulation, name="stop_simulation"),
]
