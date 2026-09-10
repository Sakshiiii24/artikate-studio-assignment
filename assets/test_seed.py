from datetime import timedelta
from django.core.management import call_command
from django.db import models
from django.test import TestCase
from django.utils import timezone

from assets.models import Asset, Employee, CheckOut


class SeedDemoDataTests(TestCase):
    """
    Tests verifying the deterministic and idempotent seed_demo_data command.
    """

    def test_seed_demo_data_creates_all_required_data(self):
        """
        Verify that seed_demo_data creates:
        - At least 8 assets
        - All 4 asset categories (CAMERA, LAPTOP, SENSOR, VEHICLE)
        - At least 4 employees
        - At least 1 inactive employee
        - At least 2 currently overdue open checkouts
        - At least 2 returned on-time checkouts
        - At least 1 returned-late checkout
        - Asset statuses consistent with their checkout state
        """
        call_command("seed_demo_data")

        # 1. Asset count >= 8
        assets = Asset.objects.all()
        self.assertGreaterEqual(assets.count(), 8)

        # 2. All 4 categories represented
        categories = set(assets.values_list("category", flat=True))
        expected_categories = {
            Asset.Category.CAMERA,
            Asset.Category.LAPTOP,
            Asset.Category.SENSOR,
            Asset.Category.VEHICLE,
        }
        self.assertTrue(
            expected_categories.issubset(categories),
            f"Expected categories {expected_categories} to be present in {categories}",
        )

        # 3. Employee count >= 4
        employees = Employee.objects.all()
        self.assertGreaterEqual(employees.count(), 4)

        # 4. At least 1 inactive employee
        inactive_employees = employees.filter(is_active=False)
        self.assertGreaterEqual(inactive_employees.count(), 1)

        # 5. At least 2 currently overdue open checkouts
        now = timezone.now()
        overdue_open = CheckOut.objects.filter(
            returned_at__isnull=True,
            due_at__lt=now,
        )
        self.assertGreaterEqual(
            overdue_open.count(),
            2,
            "Must have at least 2 currently overdue open checkouts",
        )

        # 6. At least 2 returned on-time checkouts
        returned_on_time = CheckOut.objects.filter(
            returned_at__isnull=False,
            returned_at__lte=models.F("due_at"),
        )
        self.assertGreaterEqual(
            returned_on_time.count(),
            2,
            "Must have at least 2 returned on-time checkouts",
        )

        # 7. At least 1 returned-late checkout
        returned_late = CheckOut.objects.filter(
            returned_at__isnull=False,
            returned_at__gt=models.F("due_at"),
        )
        self.assertGreaterEqual(
            returned_late.count(),
            1,
            "Must have at least 1 returned-late checkout",
        )

        # 8. Consistency between asset status and checkout state
        # All assets with open checkouts MUST have status=CHECKED_OUT
        open_checkouts = CheckOut.objects.filter(returned_at__isnull=True).select_related("asset")
        for co in open_checkouts:
            self.assertEqual(
                co.asset.status,
                Asset.Status.CHECKED_OUT,
                f"Asset {co.asset.asset_tag} with open checkout must have status CHECKED_OUT",
            )

        # Assets with NO open checkouts must NOT have status=CHECKED_OUT
        open_asset_ids = open_checkouts.values_list("asset_id", flat=True)
        non_checked_out_assets = Asset.objects.exclude(id__in=open_asset_ids)
        for asset in non_checked_out_assets:
            self.assertNotEqual(
                asset.status,
                Asset.Status.CHECKED_OUT,
                f"Asset {asset.asset_tag} without open checkout should not have status CHECKED_OUT",
            )

    def test_seed_demo_data_is_idempotent(self):
        """
        Verify that running seed_demo_data multiple times does not create duplicate
        assets, employees, or checkouts.
        """
        # First execution
        call_command("seed_demo_data")
        initial_asset_count = Asset.objects.count()
        initial_emp_count = Employee.objects.count()
        initial_checkout_count = CheckOut.objects.count()

        # Second execution
        call_command("seed_demo_data")
        self.assertEqual(
            Asset.objects.count(),
            initial_asset_count,
            "Running seed_demo_data a second time must not create duplicate assets",
        )
        self.assertEqual(
            Employee.objects.count(),
            initial_emp_count,
            "Running seed_demo_data a second time must not create duplicate employees",
        )
        self.assertEqual(
            CheckOut.objects.count(),
            initial_checkout_count,
            "Running seed_demo_data a second time must not create duplicate checkouts",
        )

        # Third execution
        call_command("seed_demo_data")
        self.assertEqual(Asset.objects.count(), initial_asset_count)
        self.assertEqual(Employee.objects.count(), initial_emp_count)
        self.assertEqual(CheckOut.objects.count(), initial_checkout_count)
