"""
URL configuration for Artikate Field Asset Check-Out Service.
"""

from django.contrib import admin
from django.urls import path, include
from assets.views import health_check

urlpatterns = [
    path('admin/', admin.site.urls),
    # Unauthenticated health check endpoint
    path('health/', health_check, name='root_health_check'),
    path('api/v1/', include('assets.urls')),
]
