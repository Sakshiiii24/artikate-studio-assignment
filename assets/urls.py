from django.urls import path
from assets.views import (
    health_check,
    AssetListCreateView,
    AssetDetailView,
    CheckOutCreateView,
    CheckOutReturnView,
    EmployeeSummaryView,
    OverdueReportView,
)

app_name = 'assets'

urlpatterns = [
    path('health/', health_check, name='health_check'),
    path('assets/', AssetListCreateView.as_view(), name='asset_list_create'),
    path('assets/<int:pk>/', AssetDetailView.as_view(), name='asset_detail'),
    path('checkouts/', CheckOutCreateView.as_view(), name='checkout_create'),
    path('checkouts/<int:pk>/return/', CheckOutReturnView.as_view(), name='checkout_return'),
    path('employees/<str:employee_code>/summary/', EmployeeSummaryView.as_view(), name='employee_summary'),
    path('reports/overdue/', OverdueReportView.as_view(), name='overdue_report'),
]
