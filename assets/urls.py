from django.urls import path
from assets.views import (
    health_check,
    CheckOutCreateView,
    CheckOutReturnView,
)

app_name = 'assets'

urlpatterns = [
    path('health/', health_check, name='health_check'),
    path('checkouts/', CheckOutCreateView.as_view(), name='checkout_create'),
    path('checkouts/<int:pk>/return/', CheckOutReturnView.as_view(), name='checkout_return'),
]
