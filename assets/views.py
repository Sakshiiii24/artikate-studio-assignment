from django.db import connection, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from assets.models import Asset, Employee, CheckOut
from assets.serializers import (
    CheckOutSerializer,
    CheckOutCreateSerializer,
    CheckOutReturnSerializer,
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

        # Enforce concurrency-safe validation and record creation inside an atomic block
        with transaction.atomic():
            # 1. Consistent Lock Ordering - Lock Employee row first
            # Rule 8: Unknown employee_code => 404 Not Found
            try:
                employee = Employee.objects.select_for_update().get(employee_code=employee_code)
            except Employee.DoesNotExist:
                return Response(
                    {"detail": f"Employee with code '{employee_code}' not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # 2. Consistent Lock Ordering - Lock Asset row second
            # Rule 8: Unknown asset_tag => 404 Not Found
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

            # Rule 3: Employee may hold at most 3 open check-outs (returned_at is null)
            # A fourth attempt => 409 Conflict
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

            # Update asset status: MAINTENANCE if flagged, else AVAILABLE
            if needs_maintenance:
                asset.status = Asset.Status.MAINTENANCE
            else:
                asset.status = Asset.Status.AVAILABLE
            asset.save(update_fields=['status', 'updated_at'])

        response_serializer = CheckOutSerializer(checkout)
        return Response(response_serializer.data, status=status.HTTP_200_OK)
