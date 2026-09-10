from datetime import timedelta
from django.db import connection, transaction
from django.db.models import (
    Avg,
    Count,
    DurationField,
    ExpressionWrapper,
    F,
    Prefetch,
    Q,
    Value,
)
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from assets.models import Asset, Employee, CheckOut
from assets.serializers import (
    AssetSerializer,
    AssetDetailSerializer,
    CheckOutSerializer,
    CheckOutCreateSerializer,
    CheckOutReturnSerializer,
    EmployeeSummarySerializer,
    OverdueReportSerializer,
)


@api_view(['GET'])
@permission_classes([AllowAny])
def health_check(request):
    """
    Health check endpoint reporting system and database connectivity.
    Per specification: Unauthenticated, returns 200 and a JSON body reporting database connectivity.
    """
    db_connected = False
    details = ""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1;")
            cursor.fetchone()
        db_connected = True
    except Exception as exc:
        details = str(exc)

    payload = {
        "status": "healthy" if db_connected else "degraded",
        "database": {
            "connected": db_connected,
            "engine": connection.settings_dict.get("ENGINE", "unknown"),
        },
    }
    if not db_connected and details:
        payload["database"]["error"] = details

    return Response(payload, status=status.HTTP_200_OK)


class AssetListCreateView(APIView):
    """
    GET  /api/v1/assets/ - List assets with pagination, status/category filter, and search.
    POST /api/v1/assets/ - Create a new asset.
    """
    permission_classes = [IsAuthenticated]
    pagination_class = PageNumberPagination

    def get(self, request):
        queryset = Asset.objects.all().order_by('id')

        # Filter by status
        status_filter = request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter.upper())

        # Filter by category
        category_filter = request.query_params.get('category')
        if category_filter:
            queryset = queryset.filter(category=category_filter.upper())

        # Search across name or asset_tag
        search_query = request.query_params.get('search')
        if search_query:
            queryset = queryset.filter(
                Q(name__icontains=search_query) | Q(asset_tag__icontains=search_query)
            )

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request)
        if page is not None:
            serializer = AssetSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)

        serializer = AssetSerializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = AssetSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class AssetDetailView(APIView):
    """
    GET /api/v1/assets/{id}/
    Retrieve asset details including current_holder (employee code and name) without N+1 queries.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            # Prefetch the single active checkout with related employee to prevent N+1 queries
            asset = Asset.objects.prefetch_related(
                Prefetch(
                    'checkouts',
                    queryset=CheckOut.objects.filter(returned_at__isnull=True).select_related('employee'),
                    to_attr='active_checkouts',
                )
            ).get(pk=pk)
        except Asset.DoesNotExist:
            return Response(
                {"detail": f"Asset with ID {pk} not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = AssetDetailSerializer(asset)
        return Response(serializer.data, status=status.HTTP_200_OK)


class CheckOutCreateView(APIView):
    """
    POST /api/v1/checkouts/
    Check out physical equipment to an employee.
    Enforces business rules 1-5, 7, and 8 with row-level database locking.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = CheckOutCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        asset_tag = serializer.validated_data['asset_tag']
        employee_code = serializer.validated_data['employee_code']
        due_at = serializer.validated_data['due_at']

        with transaction.atomic():
            # 1. Lock Employee row first (deadlock prevention hierarchy)
            try:
                employee = Employee.objects.select_for_update().get(employee_code=employee_code)
            except Employee.DoesNotExist:
                return Response(
                    {"detail": f"Employee with code '{employee_code}' not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # 2. Lock Asset row second
            try:
                asset = Asset.objects.select_for_update().get(asset_tag=asset_tag)
            except Asset.DoesNotExist:
                return Response(
                    {"detail": f"Asset with tag '{asset_tag}' not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Rule 2: Inactive employee cannot check out anything => 400 Bad Request
            if not employee.is_active:
                return Response(
                    {"detail": f"Employee '{employee.employee_code}' is inactive and cannot check out equipment."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Rule 1: Asset whose status is not AVAILABLE cannot be checked out => 409 Conflict
            if asset.status != Asset.Status.AVAILABLE:
                return Response(
                    {"detail": f"Asset '{asset.asset_tag}' is not AVAILABLE (currently {asset.status})."},
                    status=status.HTTP_409_CONFLICT,
                )

            # Rule 3: Employee may hold at most 3 open checkouts
            open_count = CheckOut.objects.filter(
                employee=employee,
                returned_at__isnull=True,
            ).count()
            if open_count >= 3:
                return Response(
                    {"detail": f"Employee '{employee.employee_code}' already holds 3 open checkouts. Limit reached."},
                    status=status.HTTP_409_CONFLICT,
                )

            # Rule 5: Create CheckOut row and set Asset status to CHECKED_OUT atomically
            checkout = CheckOut.objects.create(
                asset=asset,
                employee=employee,
                due_at=due_at,
            )
            asset.status = Asset.Status.CHECKED_OUT
            asset.save(update_fields=['status', 'updated_at'])

        response_serializer = CheckOutSerializer(checkout)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)


class CheckOutReturnView(APIView):
    """
    POST /api/v1/checkouts/{id}/return/
    Return physical equipment previously checked out.
    Enforces Rule 6: already returned => 409 Conflict, updates returned_at and asset status atomically.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        serializer = CheckOutReturnSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        condition_note = serializer.validated_data.get('condition_note', '')
        needs_maintenance = serializer.validated_data.get('needs_maintenance', False)

        with transaction.atomic():
            # 1. Lock CheckOut row
            try:
                checkout = CheckOut.objects.select_for_update().get(pk=pk)
            except CheckOut.DoesNotExist:
                return Response(
                    {"detail": f"CheckOut with ID {pk} not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Rule 6: Returning an already-returned check-out => 409 Conflict
            if checkout.returned_at is not None:
                return Response(
                    {"detail": f"CheckOut #{pk} has already been returned."},
                    status=status.HTTP_409_CONFLICT,
                )

            # 2. Lock associated Asset row
            asset = Asset.objects.select_for_update().get(pk=checkout.asset_id)

            # Update checkout returned_at and condition note
            checkout.returned_at = timezone.now()
            if condition_note:
                checkout.condition_note = condition_note
            checkout.save(update_fields=['returned_at', 'condition_note'])

            # Update asset status
            if needs_maintenance:
                asset.status = Asset.Status.MAINTENANCE
            else:
                asset.status = Asset.Status.AVAILABLE
            asset.save(update_fields=['status', 'updated_at'])

        response_serializer = CheckOutSerializer(checkout)
        return Response(response_serializer.data, status=status.HTTP_200_OK)


class EmployeeSummaryView(APIView):
    """
    GET /api/v1/employees/{employee_code}/summary/
    Compute employee checkout metrics in a single database ORM aggregation query without looping in Python.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, employee_code):
        now = timezone.now()

        # Execute single ORM query computing all four aggregate numbers
        employee_summary = Employee.objects.filter(employee_code=employee_code).annotate(
            lifetime_checkout_count=Count('checkouts'),
            currently_held=Count(
                'checkouts',
                filter=Q(checkouts__returned_at__isnull=True),
            ),
            currently_overdue=Count(
                'checkouts',
                filter=Q(
                    checkouts__returned_at__isnull=True,
                    checkouts__due_at__lt=now,
                ),
            ),
            mean_duration=Avg(
                ExpressionWrapper(
                    F('checkouts__returned_at') - F('checkouts__checked_out_at'),
                    output_field=DurationField(),
                ),
                filter=Q(checkouts__returned_at__isnull=False),
            ),
        ).first()

        if not employee_summary:
            return Response(
                {"detail": f"Employee with code '{employee_code}' not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        mean_duration_days = 0.0
        if employee_summary.mean_duration is not None:
            mean_duration_days = round(employee_summary.mean_duration.total_seconds() / 86400.0, 2)

        data = {
            "lifetime_checkout_count": employee_summary.lifetime_checkout_count,
            "currently_held": employee_summary.currently_held,
            "currently_overdue": employee_summary.currently_overdue,
            "mean_hold_duration_days": mean_duration_days,
        }
        serializer = EmployeeSummarySerializer(data=data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class OverdueReportView(APIView):
    """
    GET /api/v1/reports/overdue/
    Paginated report of all open check-outs past their due_at (most overdue first).
    Uses select_related to eliminate N+1 queries.
    """
    permission_classes = [IsAuthenticated]
    pagination_class = PageNumberPagination

    def get(self, request):
        now = timezone.now()
        queryset = (
            CheckOut.objects.filter(
                returned_at__isnull=True,
                due_at__lt=now,
            )
            .select_related('asset', 'employee')
            .annotate(
                overdue_duration=ExpressionWrapper(
                    Value(now) - F('due_at'),
                    output_field=DurationField(),
                )
            )
            .order_by('due_at')
        )

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request)
        if page is not None:
            serializer = OverdueReportSerializer(page, many=True, context={'now': now})
            return paginator.get_paginated_response(serializer.data)

        serializer = OverdueReportSerializer(queryset, many=True, context={'now': now})
        return Response(serializer.data, status=status.HTTP_200_OK)
