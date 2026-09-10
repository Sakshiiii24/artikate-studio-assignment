from datetime import date, timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from assets.models import Asset, Employee, CheckOut

User = get_user_model()


class CheckOutBusinessLogicTests(TestCase):
    """
    Test suite for checkout and return business rules and transactional atomicity.
    """

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="testuser",
            password="testpassword123",
        )
        self.client.force_authenticate(user=self.user)

        self.asset = Asset.objects.create(
            asset_tag="CAM-101",
            name="Sony A7 Camera",
            category=Asset.Category.CAMERA,
            status=Asset.Status.AVAILABLE,
            purchase_date=date(2025, 1, 1),
        )
        self.employee = Employee.objects.create(
            employee_code="EMP-001",
            full_name="Alice Walker",
            email="alice.walker@example.com",
            is_active=True,
        )

    def test_unauthenticated_request_returns_401(self):
        """Unauthenticated requests must receive HTTP 401 Unauthorized."""
        unauthenticated_client = APIClient()
        response = unauthenticated_client.post(
            "/api/v1/checkouts/",
            {
                "asset_tag": self.asset.asset_tag,
                "employee_code": self.employee.employee_code,
                "due_at": (timezone.now() + timedelta(days=5)).isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_successful_checkout(self):
        """Rule 5: Successful check-out creates CheckOut row and sets asset to CHECKED_OUT."""
        due_at = timezone.now() + timedelta(days=7)
        response = self.client.post(
            "/api/v1/checkouts/",
            {
                "asset_tag": self.asset.asset_tag,
                "employee_code": self.employee.employee_code,
                "due_at": due_at.isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["asset_tag"], self.asset.asset_tag)
        self.assertEqual(response.data["employee_code"], self.employee.employee_code)
        self.assertIsNone(response.data["returned_at"])

        # Check DB state
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, Asset.Status.CHECKED_OUT)
        self.assertTrue(
            CheckOut.objects.filter(
                id=response.data["id"],
                asset=self.asset,
                employee=self.employee,
                returned_at__isnull=True,
            ).exists()
        )

    def test_unknown_asset_returns_404(self):
        """Rule 8: Unknown asset_tag => 404 Not Found."""
        response = self.client.post(
            "/api/v1/checkouts/",
            {
                "asset_tag": "UNKNOWN-TAG",
                "employee_code": self.employee.employee_code,
                "due_at": (timezone.now() + timedelta(days=7)).isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_unknown_employee_returns_404(self):
        """Rule 8: Unknown employee_code => 404 Not Found."""
        response = self.client.post(
            "/api/v1/checkouts/",
            {
                "asset_tag": self.asset.asset_tag,
                "employee_code": "UNKNOWN-EMP",
                "due_at": (timezone.now() + timedelta(days=7)).isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_inactive_employee_returns_400(self):
        """Rule 2: Employee with is_active=False cannot check out => 400 Bad Request."""
        self.employee.is_active = False
        self.employee.save(update_fields=["is_active"])

        response = self.client.post(
            "/api/v1/checkouts/",
            {
                "asset_tag": self.asset.asset_tag,
                "employee_code": self.employee.employee_code,
                "due_at": (timezone.now() + timedelta(days=7)).isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unavailable_asset_returns_409(self):
        """Rule 1: Asset whose status is not AVAILABLE => 409 Conflict."""
        # Test CHECKED_OUT
        self.asset.status = Asset.Status.CHECKED_OUT
        self.asset.save(update_fields=["status"])

        response = self.client.post(
            "/api/v1/checkouts/",
            {
                "asset_tag": self.asset.asset_tag,
                "employee_code": self.employee.employee_code,
                "due_at": (timezone.now() + timedelta(days=7)).isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

        # Test MAINTENANCE
        self.asset.status = Asset.Status.MAINTENANCE
        self.asset.save(update_fields=["status"])

        response2 = self.client.post(
            "/api/v1/checkouts/",
            {
                "asset_tag": self.asset.asset_tag,
                "employee_code": self.employee.employee_code,
                "due_at": (timezone.now() + timedelta(days=7)).isoformat(),
            },
            format="json",
        )
        self.assertEqual(response2.status_code, status.HTTP_409_CONFLICT)

    def test_due_at_in_past_returns_400(self):
        """Rule 4: due_at in the past => 400 Bad Request."""
        past_time = timezone.now() - timedelta(minutes=10)
        response = self.client.post(
            "/api/v1/checkouts/",
            {
                "asset_tag": self.asset.asset_tag,
                "employee_code": self.employee.employee_code,
                "due_at": past_time.isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_due_at_more_than_30_days_ahead_returns_400(self):
        """Rule 4: due_at more than 30 days ahead => 400 Bad Request."""
        far_future = timezone.now() + timedelta(days=31)
        response = self.client.post(
            "/api/v1/checkouts/",
            {
                "asset_tag": self.asset.asset_tag,
                "employee_code": self.employee.employee_code,
                "due_at": far_future.isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_fourth_open_checkout_limit_returns_409(self):
        """
        Rule 3: Employee may hold at most 3 open checkouts.
        A fourth attempt must return 409 Conflict.
        """
        # Create 3 active assets and checkouts
        assets = [
            Asset.objects.create(
                asset_tag=f"EXTRA-ASSET-{i}",
                name=f"Extra Equipment {i}",
                category=Asset.Category.LAPTOP,
                status=Asset.Status.AVAILABLE,
                purchase_date=date(2025, 1, 1),
            )
            for i in range(1, 4)
        ]

        # Execute 3 successful checkouts
        for a in assets:
            res = self.client.post(
                "/api/v1/checkouts/",
                {
                    "asset_tag": a.asset_tag,
                    "employee_code": self.employee.employee_code,
                    "due_at": (timezone.now() + timedelta(days=10)).isoformat(),
                },
                format="json",
            )
            self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        # 4th checkout attempt with self.asset must fail with 409
        fourth_attempt = self.client.post(
            "/api/v1/checkouts/",
            {
                "asset_tag": self.asset.asset_tag,
                "employee_code": self.employee.employee_code,
                "due_at": (timezone.now() + timedelta(days=10)).isoformat(),
            },
            format="json",
        )
        self.assertEqual(fourth_attempt.status_code, status.HTTP_409_CONFLICT)

        # Returning one checkout should decrement active count and allow a new checkout
        first_checkout = CheckOut.objects.filter(employee=self.employee, returned_at__isnull=True).first()
        return_res = self.client.post(
            f"/api/v1/checkouts/{first_checkout.id}/return/",
            {},
            format="json",
        )
        self.assertEqual(return_res.status_code, status.HTTP_200_OK)

        # Now attempting checkout again must succeed (count is back to 2 -> 3)
        retry_attempt = self.client.post(
            "/api/v1/checkouts/",
            {
                "asset_tag": self.asset.asset_tag,
                "employee_code": self.employee.employee_code,
                "due_at": (timezone.now() + timedelta(days=10)).isoformat(),
            },
            format="json",
        )
        self.assertEqual(retry_attempt.status_code, status.HTTP_201_CREATED)

    def test_successful_return(self):
        """Rule 6: Returning sets returned_at to now and asset to AVAILABLE."""
        checkout = CheckOut.objects.create(
            asset=self.asset,
            employee=self.employee,
            due_at=timezone.now() + timedelta(days=5),
        )
        self.asset.status = Asset.Status.CHECKED_OUT
        self.asset.save(update_fields=["status"])

        response = self.client.post(
            f"/api/v1/checkouts/{checkout.id}/return/",
            {
                "condition_note": "Lens cap slightly scratched.",
                "needs_maintenance": False,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(response.data["returned_at"])

        checkout.refresh_from_db()
        self.assertIsNotNone(checkout.returned_at)
        self.assertEqual(checkout.condition_note, "Lens cap slightly scratched.")

        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, Asset.Status.AVAILABLE)

    def test_already_returned_checkout_returns_409(self):
        """Rule 6: Returning an already-returned check-out => 409 Conflict."""
        checkout = CheckOut.objects.create(
            asset=self.asset,
            employee=self.employee,
            due_at=timezone.now() + timedelta(days=5),
            returned_at=timezone.now(),
        )

        response = self.client.post(
            f"/api/v1/checkouts/{checkout.id}/return/",
            {"condition_note": "Try return again"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_return_with_needs_maintenance_true(self):
        """Rule 6: Returning with needs_maintenance=true sets asset to MAINTENANCE."""
        checkout = CheckOut.objects.create(
            asset=self.asset,
            employee=self.employee,
            due_at=timezone.now() + timedelta(days=5),
        )
        self.asset.status = Asset.Status.CHECKED_OUT
        self.asset.save(update_fields=["status"])

        response = self.client.post(
            f"/api/v1/checkouts/{checkout.id}/return/",
            {
                "condition_note": "Sensor calibration required.",
                "needs_maintenance": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, Asset.Status.MAINTENANCE)

    def test_return_without_maintenance_sets_available(self):
        """Rule 6: Returning without maintenance sets asset to AVAILABLE."""
        checkout = CheckOut.objects.create(
            asset=self.asset,
            employee=self.employee,
            due_at=timezone.now() + timedelta(days=5),
        )
        self.asset.status = Asset.Status.CHECKED_OUT
        self.asset.save(update_fields=["status"])

        response = self.client.post(
            f"/api/v1/checkouts/{checkout.id}/return/",
            {
                "condition_note": "Good condition.",
                "needs_maintenance": False,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, Asset.Status.AVAILABLE)

    def test_checkout_atomicity_on_failure(self):
        """
        Rule 5: If CheckOut creation fails, the transaction must roll back
        and the asset must NOT remain CHECKED_OUT.
        """
        self.assertEqual(self.asset.status, Asset.Status.AVAILABLE)

        with patch("assets.views.CheckOut.objects.create", side_effect=RuntimeError("Simulated database failure")):
            with self.assertRaises(RuntimeError):
                self.client.post(
                    "/api/v1/checkouts/",
                    {
                        "asset_tag": self.asset.asset_tag,
                        "employee_code": self.employee.employee_code,
                        "due_at": (timezone.now() + timedelta(days=5)).isoformat(),
                    },
                    format="json",
                )

        # Verify rollback: Asset status remains AVAILABLE
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, Asset.Status.AVAILABLE)
        self.assertEqual(CheckOut.objects.filter(asset=self.asset).count(), 0)
