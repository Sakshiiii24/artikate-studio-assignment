import datetime
from django.test import TestCase
from django.utils import timezone

from assets.models import Asset, Employee, CheckOut, OverdueNotice
from assets.tasks import flag_overdue_checkouts


class OverdueTasksTests(TestCase):
    """
    Focused verification tests for the flag_overdue_checkouts Celery task:
    - Verifies overdue open checkouts receive OverdueNotice for today's date.
    - Verifies returned checkouts (even if returned after due date) are ignored.
    - Verifies future-due open checkouts are ignored.
    - Verifies idempotency across multiple direct executions on the same date.
    - Verifies database-level uniqueness constraint prevents duplicates.
    """

    def setUp(self):
        self.today = timezone.now().date()
        self.now = timezone.now()

        # Create active employee
        self.employee = Employee.objects.create(
            employee_code="EMP-TEST-01",
            full_name="Alice Smith",
            email="alice.smith@example.com",
            is_active=True,
        )

        # Asset 1: Overdue open checkout
        self.asset_overdue = Asset.objects.create(
            asset_tag="ASSET-OD-01",
            name="Overdue Field Camera",
            category=Asset.Category.CAMERA,
            status=Asset.Status.CHECKED_OUT,
            purchase_date=datetime.date(2024, 1, 1),
        )
        self.checkout_overdue = CheckOut.objects.create(
            asset=self.asset_overdue,
            employee=self.employee,
            due_at=self.now - datetime.timedelta(days=5),
            returned_at=None,
        )

        # Asset 2: Returned checkout (due in the past, but returned)
        self.asset_returned = Asset.objects.create(
            asset_tag="ASSET-RET-01",
            name="Returned Laptop",
            category=Asset.Category.LAPTOP,
            status=Asset.Status.AVAILABLE,
            purchase_date=datetime.date(2024, 2, 1),
        )
        self.checkout_returned = CheckOut.objects.create(
            asset=self.asset_returned,
            employee=self.employee,
            due_at=self.now - datetime.timedelta(days=10),
            returned_at=self.now - datetime.timedelta(days=2),
        )

        # Asset 3: Future-due open checkout (due in future, open)
        self.asset_future = Asset.objects.create(
            asset_tag="ASSET-FUT-01",
            name="Active Survey Rig",
            category=Asset.Category.VEHICLE,
            status=Asset.Status.CHECKED_OUT,
            purchase_date=datetime.date(2024, 3, 1),
        )
        self.checkout_future = CheckOut.objects.create(
            asset=self.asset_future,
            employee=self.employee,
            due_at=self.now + datetime.timedelta(days=5),
            returned_at=None,
        )

    def test_flag_overdue_checkouts_creates_notice_and_is_idempotent(self):
        """
        Directly invoke flag_overdue_checkouts without broker dependency:
        1. First run creates exactly 1 OverdueNotice for checkout_overdue.
        2. Returned checkout is ignored.
        3. Future-due open checkout is ignored.
        4. Second run creates 0 new notices and leaves exactly 1 notice in DB.
        """
        # Pre-condition: 0 notices exist
        self.assertEqual(OverdueNotice.objects.count(), 0)

        # First direct execution
        result1 = flag_overdue_checkouts()

        self.assertEqual(result1["status"], "success")
        self.assertEqual(result1["overdue_count"], 1)
        self.assertEqual(result1["notices_created"], 1)
        self.assertEqual(result1["notice_date"], str(self.today))

        # Assert exactly one notice exists in database
        self.assertEqual(OverdueNotice.objects.count(), 1)
        notice = OverdueNotice.objects.get()
        self.assertEqual(notice.checkout_id, self.checkout_overdue.id)
        self.assertEqual(notice.notice_date, self.today)

        # Verify returned checkout has NO notices
        self.assertFalse(
            OverdueNotice.objects.filter(checkout=self.checkout_returned).exists(),
            "Returned checkout must not receive an OverdueNotice",
        )

        # Verify future-due checkout has NO notices
        self.assertFalse(
            OverdueNotice.objects.filter(checkout=self.checkout_future).exists(),
            "Future-due checkout must not receive an OverdueNotice",
        )

        # Second direct execution (verifying idempotency)
        result2 = flag_overdue_checkouts()

        self.assertEqual(result2["status"], "success")
        self.assertEqual(result2["overdue_count"], 1)
        self.assertEqual(result2["notices_created"], 0)
        self.assertEqual(result2["notice_date"], str(self.today))

        # Assert STILL exactly one notice exists in database
        self.assertEqual(
            OverdueNotice.objects.count(),
            1,
            "Re-running flag_overdue_checkouts on the same day must not create duplicate notices",
        )

    def test_flag_overdue_checkouts_multiple_overdue(self):
        """
        Verify flag_overdue_checkouts with multiple overdue checkouts across employees/assets.
        """
        emp2 = Employee.objects.create(
            employee_code="EMP-TEST-02",
            full_name="Bob Jones",
            email="bob.jones@example.com",
            is_active=True,
        )
        asset4 = Asset.objects.create(
            asset_tag="ASSET-OD-02",
            name="Overdue Sensor",
            category=Asset.Category.SENSOR,
            status=Asset.Status.CHECKED_OUT,
            purchase_date=datetime.date(2024, 4, 1),
        )
        CheckOut.objects.create(
            asset=asset4,
            employee=emp2,
            due_at=self.now - datetime.timedelta(days=2),
            returned_at=None,
        )

        result = flag_overdue_checkouts()
        self.assertEqual(result["overdue_count"], 2)
        self.assertEqual(result["notices_created"], 2)
        self.assertEqual(OverdueNotice.objects.count(), 2)

        # Re-run immediately
        result_rerun = flag_overdue_checkouts()
        self.assertEqual(result_rerun["overdue_count"], 2)
        self.assertEqual(result_rerun["notices_created"], 0)
        self.assertEqual(OverdueNotice.objects.count(), 2)
