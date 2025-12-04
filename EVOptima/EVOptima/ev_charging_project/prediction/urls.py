from django.urls import path
from . import views

app_name = 'prediction'

urlpatterns = [
    path('', views.predict_view, name='predict'),
    path('welcome/', views.welcome_view, name='welcome'),
]
