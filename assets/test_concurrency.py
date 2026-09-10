import threading
import unittest
from datetime import date, timedelta
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from assets.models import Asset, Employee, CheckOut

User = get_user_model()


class ConcurrencyLockingTests(TransactionTestCase):
    """
    Multithreaded concurrency tests verifying PostgreSQL row-level locking (SELECT FOR UPDATE).
    
    CRITICAL NOTE:
    These tests MUST execute against PostgreSQL, because PostgreSQL supports true row-level locks
    and MVCC. SQLite uses database file locks that raise 'OperationalError: database is locked'
    under concurrent thread writes.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="concurrency_user",
            password="testpassword123",
        )
        self.asset = Asset.objects.create(
            asset_tag="CONC-CAM-01",
            name="Concurrency Test Camera",
            category=Asset.Category.CAMERA,
            status=Asset.Status.AVAILABLE,
            purchase_date=date(2025, 1, 1),
        )
        self.emp1 = Employee.objects.create(
            employee_code="CONC-EMP-01",
            full_name="Worker One",
            email="worker1@example.com",
            is_active=True,
        )
        self.emp2 = Employee.objects.create(
            employee_code="CONC-EMP-02",
            full_name="Worker Two",
            email="worker2@example.com",
            is_active=True,
        )

    @unittest.skipIf(
        connection.vendor == 'sqlite',
        "Concurrency tests require PostgreSQL row-level locking (select_for_update) and cannot execute under SQLite file-level locking."
    )
    def test_concurrent_checkout_same_asset_exactly_one_succeeds(self):
        """
        Rule 7 & Assessment A5:
        When two checkout requests for the SAME asset arrive at the exact same moment,
        exactly one must succeed (201) and the other must receive HTTP 409 Conflict.
        """
        results = []
        barrier = threading.Barrier(2)

        def attempt_checkout(employee_code):
            client = APIClient()
            client.force_authenticate(user=self.user)
            connection.close()  # Ensure dedicated connection for this thread
            try:
                barrier.wait(timeout=5)
                res = client.post(
                    "/api/v1/checkouts/",
                    {
                        "asset_tag": self.asset.asset_tag,
                        "employee_code": employee_code,
                        "due_at": (timezone.now() + timedelta(days=7)).isoformat(),
                    },
                    format="json",
                )
                results.append(res.status_code)
            finally:
                connection.close()

        t1 = threading.Thread(target=attempt_checkout, args=(self.emp1.employee_code,))
        t2 = threading.Thread(target=attempt_checkout, args=(self.emp2.employee_code,))

        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        self.assertEqual(len(results), 2)
        self.assertEqual(results.count(status.HTTP_201_CREATED), 1)
        self.assertEqual(results.count(status.HTTP_409_CONFLICT), 1)

        # Confirm database state
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, Asset.Status.CHECKED_OUT)
        self.assertEqual(CheckOut.objects.filter(asset=self.asset).count(), 1)

    @unittest.skipIf(
        connection.vendor == 'sqlite',
        "Concurrency tests require PostgreSQL row-level locking (select_for_update) and cannot execute under SQLite file-level locking."
    )
    def test_concurrent_checkout_employee_limit_race_cannot_exceed_limit(self):
        """
        Rule 3 Concurrency:
        An employee with 2 open checkouts attempts two simultaneous new checkouts for different assets.
        Locking the Employee row with select_for_update() must serialize both requests, ensuring
        exactly one succeeds (201) and the second receives 409 Conflict, so the total open checkouts
        never exceeds 3.
        """
        # Create 2 initial open checkouts for emp1
        for i in range(1, 3):
            init_asset = Asset.objects.create(
                asset_tag=f"INIT-ASSET-{i}",
                name=f"Initial Asset {i}",
                category=Asset.Category.LAPTOP,
                status=Asset.Status.AVAILABLE,
                purchase_date=date(2025, 1, 1),
            )
            CheckOut.objects.create(
                asset=init_asset,
                employee=self.emp1,
                due_at=timezone.now() + timedelta(days=5),
            )
            init_asset.status = Asset.Status.CHECKED_OUT
            init_asset.save(update_fields=["status"])

        # Create 2 new available assets to be checked out simultaneously
        asset_a = Asset.objects.create(
            asset_tag="RACE-ASSET-A",
            name="Race Asset A",
            category=Asset.Category.SENSOR,
            status=Asset.Status.AVAILABLE,
            purchase_date=date(2025, 1, 1),
        )
        asset_b = Asset.objects.create(
            asset_tag="RACE-ASSET-B",
            name="Race Asset B",
            category=Asset.Category.VEHICLE,
            status=Asset.Status.AVAILABLE,
            purchase_date=date(2025, 1, 1),
        )

        results = []
        barrier = threading.Barrier(2)

        def attempt_race_checkout(asset_tag):
            client = APIClient()
            client.force_authenticate(user=self.user)
            connection.close()
            try:
                barrier.wait(timeout=5)
                res = client.post(
                    "/api/v1/checkouts/",
                    {
                        "asset_tag": asset_tag,
                        "employee_code": self.emp1.employee_code,
                        "due_at": (timezone.now() + timedelta(days=7)).isoformat(),
                    },
                    format="json",
                )
                results.append(res.status_code)
            finally:
                connection.close()

        t1 = threading.Thread(target=attempt_race_checkout, args=(asset_a.asset_tag,))
        t2 = threading.Thread(target=attempt_race_checkout, args=(asset_b.asset_tag,))

        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        self.assertEqual(len(results), 2)
        # Exactly one must succeed, one must hit 409 limit
        self.assertEqual(results.count(status.HTTP_201_CREATED), 1)
        self.assertEqual(results.count(status.HTTP_409_CONFLICT), 1)

        # Total open checkouts must be exactly 3, NEVER 4
        open_checkouts = CheckOut.objects.filter(employee=self.emp1, returned_at__isnull=True).count()
        self.assertEqual(open_checkouts, 3)
