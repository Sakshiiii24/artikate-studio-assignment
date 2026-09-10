from datetime import date, timedelta
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import TestCase
from django.utils import timezone

from assets.models import Asset, Employee, CheckOut, OverdueNotice


class ModelStructureTests(TestCase):
    """
    Structural verification tests for model fields, uniqueness,
    database constraints, and relational integrity (PROTECT and CASCADE).
    """

    def setUp(self):
        self.asset = Asset.objects.create(
            asset_tag="CAM-001",
            name="Sony A7 IV",
            category=Asset.Category.CAMERA,
            status=Asset.Status.AVAILABLE,
            purchase_date=date(2025, 1, 15),
        )
        self.employee = Employee.objects.create(
            employee_code="EMP-101",
            full_name="Jane Doe",
            email="jane.doe@example.com",
            is_active=True,
        )
        self.checkout = CheckOut.objects.create(
            asset=self.asset,
            employee=self.employee,
            due_at=timezone.now() + timedelta(days=7),
        )

    def test_unique_asset_tag(self):
        """Creating an asset with an existing asset_tag must raise IntegrityError."""
        with self.assertRaises(IntegrityError):
            Asset.objects.create(
                asset_tag="CAM-001",
                name="Duplicate Tag Camera",
                category=Asset.Category.CAMERA,
                status=Asset.Status.AVAILABLE,
                purchase_date=date(2025, 2, 1),
            )

    def test_unique_employee_code(self):
        """Creating an employee with an existing employee_code must raise IntegrityError."""
        with self.assertRaises(IntegrityError):
            Employee.objects.create(
                employee_code="EMP-101",
                full_name="John Smith",
                email="john.smith@example.com",
                is_active=True,
            )

    def test_unique_employee_email(self):
        """Creating an employee with an existing email must raise IntegrityError."""
        with self.assertRaises(IntegrityError):
            Employee.objects.create(
                employee_code="EMP-102",
                full_name="Another Employee",
                email="jane.doe@example.com",
                is_active=True,
            )

    def test_unique_overdue_notice_per_checkout_and_date(self):
        """Creating two notices for the same (checkout, notice_date) must raise IntegrityError."""
        today = timezone.now().date()
        OverdueNotice.objects.create(checkout=self.checkout, notice_date=today)

        with self.assertRaises(IntegrityError):
            OverdueNotice.objects.create(checkout=self.checkout, notice_date=today)

    def test_protect_behavior_for_asset(self):
        """Deleting an asset referenced by a CheckOut must raise ProtectedError."""
        with self.assertRaises(ProtectedError):
            self.asset.delete()

    def test_protect_behavior_for_employee(self):
        """Deleting an employee referenced by a CheckOut must raise ProtectedError."""
        with self.assertRaises(ProtectedError):
            self.employee.delete()

    def test_cascade_behavior_for_overdue_notice(self):
        """Deleting a CheckOut must CASCADE and delete related OverdueNotice records."""
        today = timezone.now().date()
        notice = OverdueNotice.objects.create(checkout=self.checkout, notice_date=today)
        notice_id = notice.id

        # Delete the checkout
        self.checkout.delete()

        # The notice should be deleted via CASCADE
        self.assertFalse(OverdueNotice.objects.filter(id=notice_id).exists())
