# File: ev_charging_project/ev_charging_project/urls.py
from django.contrib import admin
from django.urls import path
from prediction import views
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.predict_view, name='predict'),
] + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)