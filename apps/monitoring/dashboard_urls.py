"""WebSocket dashboard page route: ``/monitoring/dashboard/``.

Separate module so the dashboard can be mounted outside the API prefix without
re-registering the ``monitoring`` namespace (Django check ``urls.W005``).
"""
from django.urls import path

from . import views

urlpatterns = [
    path("dashboard/", views.dashboard, name="monitoring_dashboard"),
]
