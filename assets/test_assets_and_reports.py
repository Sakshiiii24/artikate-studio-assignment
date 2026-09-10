from datetime import date, timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from assets.models import Asset, Employee, CheckOut

User = get_user_model()


class AssetAPITests(TestCase):
    """
    Tests for POST /api/v1/assets/, GET /api/v1/assets/, and GET /api/v1/assets/{id}/.
    """

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="asset_tester", password="password123")
        self.client.force_authenticate(user=self.user)

    def test_create_asset_success(self):
        """POST /api/v1/assets/ successfully creates an asset and defaults status to AVAILABLE."""
        payload = {
            "asset_tag": "NEW-CAM-01",
            "name": "Nikon Z9 Camera",
            "category": "CAMERA",
            "purchase_date": "2025-02-15",
        }
        response = self.client.post("/api/v1/assets/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["asset_tag"], "NEW-CAM-01")
        self.assertEqual(response.data["status"], "AVAILABLE")
        self.assertTrue(Asset.objects.filter(asset_tag="NEW-CAM-01").exists())

    def test_create_asset_duplicate_tag_returns_400(self):
        """POST /api/v1/assets/ with duplicate asset_tag returns HTTP 400."""
        Asset.objects.create(
            asset_tag="DUP-TAG-01",
            name="Existing Camera",
            category=Asset.Category.CAMERA,
            status=Asset.Status.AVAILABLE,
            purchase_date=date(2025, 1, 1),
        )
        payload = {
            "asset_tag": "DUP-TAG-01",
            "name": "Another Camera",
            "category": "CAMERA",
            "purchase_date": "2025-02-01",
        }
        response = self.client.post("/api/v1/assets/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("asset_tag", response.data)

    def test_create_asset_invalid_category_returns_400(self):
        """POST /api/v1/assets/ with invalid category returns HTTP 400."""
        payload = {
            "asset_tag": "INVALID-CAT",
            "name": "Drone 1",
            "category": "DRONE",  # Invalid choice
            "purchase_date": "2025-02-01",
        }
        response = self.client.post("/api/v1/assets/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("category", response.data)

    def test_list_assets_pagination(self):
        """GET /api/v1/assets/ is paginated with PAGE_SIZE=20."""
        for i in range(25):
            Asset.objects.create(
                asset_tag=f"PAG-ASSET-{i:02d}",
                name=f"Paginated Asset {i}",
                category=Asset.Category.LAPTOP,
                purchase_date=date(2025, 1, 1),
            )

        response = self.client.get("/api/v1/assets/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 25)
        self.assertEqual(len(response.data["results"]), 20)
        self.assertIsNotNone(response.data["next"])

    def test_list_assets_status_filter(self):
        """GET /api/v1/assets/?status=MAINTENANCE filters accurately."""
        Asset.objects.create(
            asset_tag="AVAIL-01",
            name="Available Laptop",
            category=Asset.Category.LAPTOP,
            status=Asset.Status.AVAILABLE,
            purchase_date=date(2025, 1, 1),
        )
        Asset.objects.create(
            asset_tag="MAINT-01",
            name="Broken Laptop",
            category=Asset.Category.LAPTOP,
            status=Asset.Status.MAINTENANCE,
            purchase_date=date(2025, 1, 1),
        )

        response = self.client.get("/api/v1/assets/?status=MAINTENANCE")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["asset_tag"], "MAINT-01")

    def test_list_assets_category_filter(self):
        """GET /api/v1/assets/?category=VEHICLE filters accurately."""
        Asset.objects.create(
            asset_tag="VEH-01",
            name="Inspection Van",
            category=Asset.Category.VEHICLE,
            purchase_date=date(2025, 1, 1),
        )
        Asset.objects.create(
            asset_tag="SEN-01",
            name="Thermal Sensor",
            category=Asset.Category.SENSOR,
            purchase_date=date(2025, 1, 1),
        )

        response = self.client.get("/api/v1/assets/?category=VEHICLE")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["asset_tag"], "VEH-01")

    def test_list_assets_search_by_name_and_tag(self):
        """GET /api/v1/assets/?search=... searches across both name and asset_tag."""
        Asset.objects.create(
            asset_tag="SONY-CAM-99",
            name="Alpha Camera",
            category=Asset.Category.CAMERA,
            purchase_date=date(2025, 1, 1),
        )
        Asset.objects.create(
            asset_tag="CANON-01",
            name="Sony Compatible Lens",
            category=Asset.Category.CAMERA,
            purchase_date=date(2025, 1, 1),
        )
        Asset.objects.create(
            asset_tag="DELL-01",
            name="Precision Laptop",
            category=Asset.Category.LAPTOP,
            purchase_date=date(2025, 1, 1),
        )

        # Search by tag
        res_tag = self.client.get("/api/v1/assets/?search=SONY-CAM")
        self.assertEqual(res_tag.status_code, status.HTTP_200_OK)
        self.assertEqual(res_tag.data["count"], 1)
        self.assertEqual(res_tag.data["results"][0]["asset_tag"], "SONY-CAM-99")

        # Search by name
        res_name = self.client.get("/api/v1/assets/?search=Precision")
        self.assertEqual(res_name.status_code, status.HTTP_200_OK)
        self.assertEqual(res_name.data["count"], 1)
        self.assertEqual(res_name.data["results"][0]["asset_tag"], "DELL-01")

    def test_asset_detail_without_current_holder(self):
        """GET /api/v1/assets/{id}/ returns current_holder as null when available."""
        asset = Asset.objects.create(
            asset_tag="FREE-01",
            name="Unassigned Sensor",
            category=Asset.Category.SENSOR,
            status=Asset.Status.AVAILABLE,
            purchase_date=date(2025, 1, 1),
        )
        response = self.client.get(f"/api/v1/assets/{asset.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["current_holder"])

    def test_asset_detail_with_current_holder(self):
        """GET /api/v1/assets/{id}/ returns current_holder employee details when checked out."""
        asset = Asset.objects.create(
            asset_tag="HELD-01",
            name="Field Van",
            category=Asset.Category.VEHICLE,
            status=Asset.Status.CHECKED_OUT,
            purchase_date=date(2025, 1, 1),
        )
        employee = Employee.objects.create(
            employee_code="EMP-HOLD-1",
            full_name="Holder Employee",
            email="holder@example.com",
            is_active=True,
        )
        CheckOut.objects.create(
            asset=asset,
            employee=employee,
            due_at=timezone.now() + timedelta(days=5),
        )

        response = self.client.get(f"/api/v1/assets/{asset.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(response.data["current_holder"])
        self.assertEqual(response.data["current_holder"]["employee_code"], "EMP-HOLD-1")
        self.assertEqual(response.data["current_holder"]["employee_name"], "Holder Employee")

    def test_asset_detail_returned_checkout_does_not_count_as_current_holder(self):
        """GET /api/v1/assets/{id}/ returns current_holder as null if checkout was returned."""
        asset = Asset.objects.create(
            asset_tag="PREV-HELD-01",
            name="Returned Drone",
            category=Asset.Category.CAMERA,
            status=Asset.Status.AVAILABLE,
            purchase_date=date(2025, 1, 1),
        )
        employee = Employee.objects.create(
            employee_code="EMP-PAST-1",
            full_name="Past Holder",
            email="pastholder@example.com",
            is_active=True,
        )
        CheckOut.objects.create(
            asset=asset,
            employee=employee,
            due_at=timezone.now() - timedelta(days=2),
            returned_at=timezone.now() - timedelta(days=1),
        )

        response = self.client.get(f"/api/v1/assets/{asset.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["current_holder"])

    def test_asset_detail_unknown_id_returns_404(self):
        """GET /api/v1/assets/{id}/ with non-existent ID returns HTTP 404."""
        response = self.client.get("/api/v1/assets/999999/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class EmployeeSummaryAPITests(TestCase):
    """
    Tests for GET /api/v1/employees/{employee_code}/summary/.
    Verifies single-query ORM aggregation, correct metric math, and edge cases.
    """

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="summary_tester", password="password123")
        self.client.force_authenticate(user=self.user)

        self.employee = Employee.objects.create(
            employee_code="EMP-SUM-01",
            full_name="Summary Target",
            email="target@example.com",
            is_active=True,
        )
        self.other_employee = Employee.objects.create(
            employee_code="EMP-OTHER-01",
            full_name="Other Employee",
            email="other@example.com",
            is_active=True,
        )

    def test_summary_all_metrics_and_single_query(self):
        """
        Verify all 4 metrics calculated by database in a single query:
        - lifetime_checkout_count = 4
        - currently_held = 2 (open checkouts)
        - currently_overdue = 1 (open checkout where due_at < now)
        - mean_hold_duration_days = 3.0 (from 2 returned checkouts: 2 days + 4 days -> mean 3 days)
        """
        now = timezone.now()

        # Item 1: returned after 2 days
        a1 = Asset.objects.create(asset_tag="SUM-A1", name="A1", category="LAPTOP", purchase_date="2025-01-01")
        c1 = CheckOut.objects.create(asset=a1, employee=self.employee, due_at=now + timedelta(days=2))
        CheckOut.objects.filter(id=c1.id).update(
            checked_out_at=now - timedelta(days=10),
            returned_at=now - timedelta(days=8),
        )

        # Item 2: returned after 4 days
        a2 = Asset.objects.create(asset_tag="SUM-A2", name="A2", category="CAMERA", purchase_date="2025-01-01")
        c2 = CheckOut.objects.create(asset=a2, employee=self.employee, due_at=now + timedelta(days=2))
        CheckOut.objects.filter(id=c2.id).update(
            checked_out_at=now - timedelta(days=12),
            returned_at=now - timedelta(days=8),
        )

        # Item 3: currently held, on-time (due in 5 days)
        a3 = Asset.objects.create(asset_tag="SUM-A3", name="A3", category="SENSOR", purchase_date="2025-01-01")
        CheckOut.objects.create(asset=a3, employee=self.employee, due_at=now + timedelta(days=5))

        # Item 4: currently held, overdue (due 3 days ago)
        a4 = Asset.objects.create(asset_tag="SUM-A4", name="A4", category="VEHICLE", purchase_date="2025-01-01")
        CheckOut.objects.create(asset=a4, employee=self.employee, due_at=now - timedelta(days=3))

        # Item 5: Checkout for ANOTHER employee (must not affect this summary)
        a5 = Asset.objects.create(asset_tag="SUM-A5", name="A5", category="LAPTOP", purchase_date="2025-01-01")
        CheckOut.objects.create(asset=a5, employee=self.other_employee, due_at=now - timedelta(days=2))

        # Verify query count is strictly 1
        with self.assertNumQueries(1):
            response = self.client.get(f"/api/v1/employees/{self.employee.employee_code}/summary/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        self.assertEqual(data["lifetime_checkout_count"], 4)
        self.assertEqual(data["currently_held"], 2)
        self.assertEqual(data["currently_overdue"], 1)
        self.assertAlmostEqual(data["mean_hold_duration_days"], 3.0, places=2)

    def test_summary_due_exactly_now_is_not_overdue(self):
        """Assessment requirement: An item due exactly now is NOT overdue."""
        now = timezone.now()
        asset = Asset.objects.create(asset_tag="EXACT-NOW", name="Asset Now", category="LAPTOP", purchase_date="2025-01-01")
        CheckOut.objects.create(asset=asset, employee=self.employee, due_at=now)

        with patch("assets.views.timezone.now", return_value=now):
            response = self.client.get(f"/api/v1/employees/{self.employee.employee_code}/summary/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["currently_held"], 1)
        self.assertEqual(response.data["currently_overdue"], 0)

    def test_summary_unknown_employee_returns_404(self):
        """Unknown employee_code returns HTTP 404."""
        response = self.client.get("/api/v1/employees/NONEXISTENT/summary/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class OverdueReportAPITests(TestCase):
    """
    Tests for GET /api/v1/reports/overdue/.
    Verifies filtering, ordering, fields, and N+1 query elimination.
    """

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="report_tester", password="password123")
        self.client.force_authenticate(user=self.user)

        self.employee = Employee.objects.create(
            employee_code="EMP-REP-01",
            full_name="Report Officer",
            email="report.officer@example.com",
            is_active=True,
        )

    def test_overdue_report_filtering_and_ordering(self):
        """
        Overdue report returns only open past-due checkouts, ordered most overdue first.
        Does not include returned overdue checkouts, on-time checkouts, or items due now.
        """
        now = timezone.now()

        # Item 1: Open, due 10 days ago (most overdue)
        a1 = Asset.objects.create(asset_tag="OVER-10", name="Overdue 10d", category="CAMERA", purchase_date="2025-01-01")
        c1 = CheckOut.objects.create(asset=a1, employee=self.employee, due_at=now - timedelta(days=10))

        # Item 2: Open, due 3 days ago
        a2 = Asset.objects.create(asset_tag="OVER-03", name="Overdue 3d", category="LAPTOP", purchase_date="2025-01-01")
        c2 = CheckOut.objects.create(asset=a2, employee=self.employee, due_at=now - timedelta(days=3))

        # Item 3: Returned overdue checkout (must NOT appear in report)
        a3 = Asset.objects.create(asset_tag="RET-OVER", name="Returned Overdue", category="SENSOR", purchase_date="2025-01-01")
        CheckOut.objects.create(
            asset=a3,
            employee=self.employee,
            due_at=now - timedelta(days=5),
            returned_at=now - timedelta(days=1),
        )

        # Item 4: Open checkout due in future (must NOT appear)
        a4 = Asset.objects.create(asset_tag="FUTURE-01", name="Future Asset", category="VEHICLE", purchase_date="2025-01-01")
        CheckOut.objects.create(asset=a4, employee=self.employee, due_at=now + timedelta(days=5))

        # Item 5: Open checkout due exactly now (must NOT appear)
        a5 = Asset.objects.create(asset_tag="NOW-01", name="Due Now Asset", category="LAPTOP", purchase_date="2025-01-01")
        CheckOut.objects.create(asset=a5, employee=self.employee, due_at=now)

        with patch("assets.views.timezone.now", return_value=now):
            response = self.client.get("/api/v1/reports/overdue/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]

        # Only Item 1 and Item 2 should appear
        self.assertEqual(len(results), 2)

        # Most overdue first: c1 (10 days overdue) should be before c2 (3 days overdue)
        self.assertEqual(results[0]["checkout_id"], c1.id)
        self.assertEqual(results[0]["asset_tag"], "OVER-10")
        self.assertEqual(results[0]["asset_name"], "Overdue 10d")
        self.assertEqual(results[0]["employee_code"], "EMP-REP-01")
        self.assertEqual(results[0]["employee_name"], "Report Officer")
        self.assertGreaterEqual(results[0]["days_overdue"], 9)  # approx 10 days

        self.assertEqual(results[1]["checkout_id"], c2.id)
        self.assertEqual(results[1]["asset_tag"], "OVER-03")

    def test_overdue_report_no_n_plus_one_queries(self):
        """
        Verify that fetching overdue report issues a constant number of queries (2 queries: count + select_related),
        proving absence of N+1 database queries.
        """
        now = timezone.now()
        for i in range(8):
            asset = Asset.objects.create(
                asset_tag=f"N1-ASSET-{i}",
                name=f"N1 Asset {i}",
                category="CAMERA",
                purchase_date="2025-01-01",
            )
            CheckOut.objects.create(
                asset=asset,
                employee=self.employee,
                due_at=now - timedelta(days=i + 1),
            )

        # Paginated response requires 1 COUNT query + 1 SELECT related query = 2 queries total
        with self.assertNumQueries(2):
            response = self.client.get("/api/v1/reports/overdue/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 8)

    def test_overdue_report_pagination(self):
        """GET /api/v1/reports/overdue/ respects standard PAGE_SIZE=20 pagination."""
        now = timezone.now()
        for i in range(25):
            asset = Asset.objects.create(
                asset_tag=f"PAG-OVER-{i:02d}",
                name=f"Paginated Overdue {i}",
                category="SENSOR",
                purchase_date="2025-01-01",
            )
            CheckOut.objects.create(
                asset=asset,
                employee=self.employee,
                due_at=now - timedelta(days=i + 1),
            )

        response = self.client.get("/api/v1/reports/overdue/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 25)
        self.assertEqual(len(response.data["results"]), 20)
        self.assertIsNotNone(response.data["next"])
