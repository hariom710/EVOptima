# File: ev_charging_project/ev_charging_project/urls.py
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from accounts.views import root_redirect
from prediction.views import home_view, welcome_view, dashboard_view

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('', root_redirect, name='root'),
    path('home/', home_view, name='home'),
    path('welcome/', welcome_view, name='welcome'),
    path('dashboard/', dashboard_view, name='dashboard'),  # legacy redirect in view
    path('prediction/', include('prediction.urls')),
    path('api/monitoring/', include('monitoring.urls')),
    path('visualization/', include('visualization.urls')),
] + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)