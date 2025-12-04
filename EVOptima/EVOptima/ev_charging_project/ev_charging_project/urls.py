# File: ev_charging_project/ev_charging_project/urls.py
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from accounts.views import root_redirect
from prediction.views import dashboard_view, welcome_view

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('', root_redirect, name='root'),
    path('welcome/', welcome_view, name='welcome'),
    path('dashboard/', dashboard_view, name='dashboard'),
    path('api/monitoring/', include('monitoring.urls')),
    path('visualization/', include('visualization.urls')),
] + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)