"""fault2's short API aliases: ``/api/status/``, ``/api/thresholds/``, ``/api/events/``.

Same handlers as the canonical ``monitoring:`` routes; kept because
``templates/monitoring/dashboard.html`` calls them verbatim. Registered without
an ``app_name`` so this module can be included exactly once.
"""
from django.urls import path

from . import views

urlpatterns = [
    path("status/", views.status, name="monitoring_status"),
    path("thresholds/", views.thresholds_view, name="monitoring_thresholds"),
    path("events/", views.events, name="monitoring_events"),
    # fault2 also served the dashboard under this prefix (its urlconf used
    # path('', dashboard) under 'api/'). The bare '/' route is superseded by
    # project A's root redirect; /monitoring/dashboard/ is canonical.
    path("dashboard/", views.dashboard, name="monitoring_dashboard_alias"),
]
