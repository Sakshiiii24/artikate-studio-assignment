from datetime import timedelta
from django.utils import timezone
from rest_framework import serializers

from assets.models import Asset, Employee, CheckOut


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
