from django.urls import path
from .views import health_check

app_name = 'assets'

urlpatterns = [
    path('health/', health_check, name='health_check'),
]
