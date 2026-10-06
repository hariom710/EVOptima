"""Root URL configuration for EVOptima.

Route families:

===============  =============================================  =====================
Path             Source                                         Purpose
===============  =============================================  =====================
``/``            project A                                      root redirect
``/home/``       project A                                      status + recent events
``/accounts/``   project A                                      login / register / logout
``/prediction/`` project A                                      prediction forms
``/scheduling/``  project A                                     smart charging scheduler
``/visualization/`` project A                                   charts + sim controls
``/api/monitoring/`` project A (canonical monitoring namespace)  status/thresholds/events
``/api/``        fault2 alias namespace                          short routes for dashboard
``/monitoring/dashboard/`` fault2                               WebSocket dashboard
``/admin/``      Django admin                                   admin
===============  =============================================  =====================
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.accounts.views import root_redirect
from apps.prediction.views import dashboard_view, home_view, welcome_view

urlpatterns = [
    path("admin/", admin.site.urls),
    # accounts
    path("accounts/", include("apps.accounts.urls")),
    path("", root_redirect, name="root"),
    # prediction
    path("home/", home_view, name="home"),
    path("welcome/", welcome_view, name="welcome"),
    path("dashboard/", dashboard_view, name="dashboard"),
    path("prediction/", include("apps.prediction.urls")),
    path("scheduling/", include("apps.scheduling.urls")),
    path("visualization/", include("apps.visualization.urls")),
    # monitoring — canonical namespace (apps.monitoring.urls has app_name)
    path("api/monitoring/", include("apps.monitoring.urls")),
    # monitoring — fault2 short aliases (/api/status/, /api/thresholds/, /api/events/)
    path("api/", include("apps.monitoring.api_urls")),
    # monitoring — WebSocket dashboard page
    path("monitoring/", include("apps.monitoring.dashboard_urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
