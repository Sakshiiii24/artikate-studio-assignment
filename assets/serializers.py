from datetime import timedelta
from django.utils import timezone
from rest_framework import serializers

from assets.models import Asset, Employee, CheckOut


class AssetSerializer(serializers.ModelSerializer):
    """
    Serializer for Asset list and creation.
    """
    class Meta:
        model = Asset
        fields = [
            'id',
            'asset_tag',
            'name',
            'category',
            'status',
            'purchase_date',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class AssetDetailSerializer(serializers.ModelSerializer):
    """
    Serializer for Asset detail view, including current_holder.
    """
    current_holder = serializers.SerializerMethodField()

    class Meta:
        model = Asset
        fields = [
            'id',
            'asset_tag',
            'name',
            'category',
            'status',
            'purchase_date',
            'current_holder',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_current_holder(self, obj):
        # Support prefetched active checkouts to eliminate N+1 queries
        if hasattr(obj, 'active_checkouts'):
            active = obj.active_checkouts[0] if obj.active_checkouts else None
        else:
            active = obj.checkouts.filter(returned_at__isnull=True).select_related('employee').first()

        if active and active.employee:
            return {
                "employee_code": active.employee.employee_code,
                "employee_name": active.employee.full_name,
            }
        return None


class CheckOutSerializer(serializers.ModelSerializer):
    """
    Serializer for CheckOut details in API responses.
    """
    asset_tag = serializers.CharField(source='asset.asset_tag', read_only=True)
    asset_name = serializers.CharField(source='asset.name', read_only=True)
    employee_code = serializers.CharField(source='employee.employee_code', read_only=True)
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)

    class Meta:
        model = CheckOut
        fields = [
            'id',
            'asset_tag',
            'asset_name',
            'employee_code',
            'employee_name',
            'checked_out_at',
            'due_at',
            'returned_at',
            'condition_note',
        ]
        read_only_fields = ['id', 'checked_out_at', 'returned_at']


class CheckOutCreateSerializer(serializers.Serializer):
    """
    Serializer and request validator for POST /api/v1/checkouts/.
    """
    asset_tag = serializers.CharField(max_length=32)
    employee_code = serializers.CharField(max_length=16)
    due_at = serializers.DateTimeField()

    def validate_due_at(self, value):
        """
        Validate Rule 4:
        due_at must be in the future and no more than 30 days ahead of now.
        """
        now = timezone.now()
        if value <= now:
            raise serializers.ValidationError("due_at must be in the future.")
        if value > now + timedelta(days=30):
            raise serializers.ValidationError("due_at cannot be more than 30 days in the future.")
        return value


class CheckOutReturnSerializer(serializers.Serializer):
    """
    Serializer and request validator for POST /api/v1/checkouts/{id}/return/.
    """
    condition_note = serializers.CharField(required=False, allow_blank=True, default="")
    needs_maintenance = serializers.BooleanField(required=False, default=False)


class EmployeeSummarySerializer(serializers.Serializer):
    """
    Serializer for Employee Summary endpoint.
    """
    lifetime_checkout_count = serializers.IntegerField()
    currently_held = serializers.IntegerField()
    currently_overdue = serializers.IntegerField()
    mean_hold_duration_days = serializers.FloatField()


class OverdueReportSerializer(serializers.ModelSerializer):
    """
    Serializer for Overdue Report endpoint.
    Uses consistent reference timestamp from query annotation or serializer context.
    """
    checkout_id = serializers.IntegerField(source='id', read_only=True)
    asset_tag = serializers.CharField(source='asset.asset_tag', read_only=True)
    asset_name = serializers.CharField(source='asset.name', read_only=True)
    employee_code = serializers.CharField(source='employee.employee_code', read_only=True)
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    days_overdue = serializers.SerializerMethodField()

    class Meta:
        model = CheckOut
        fields = [
            'id',
            'checkout_id',
            'asset_tag',
            'asset_name',
            'employee_code',
            'employee_name',
            'due_at',
            'days_overdue',
        ]

    def get_days_overdue(self, obj):
        # 1. Prefer database-annotated duration calculated with the exact queryset reference timestamp
        if hasattr(obj, 'overdue_duration') and obj.overdue_duration is not None:
            return obj.overdue_duration.days
        # 2. Fallback to exact reference timestamp passed via serializer context
        now = self.context.get('now') or timezone.now()
        return (now - obj.due_at).days
