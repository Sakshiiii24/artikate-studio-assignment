import datetime
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from assets.models import Asset, Employee, CheckOut


DEMO_ASSETS = [
    {
        "asset_tag": "DEMO-CAM-01",
        "name": "Sony Alpha A7 IV Field Camera",
        "category": Asset.Category.CAMERA,
        "status": Asset.Status.CHECKED_OUT,
        "purchase_date": datetime.date(2024, 1, 15),
    },
    {
        "asset_tag": "DEMO-CAM-02",
        "name": "Canon EOS R6 Mark II Camera",
        "category": Asset.Category.CAMERA,
        "status": Asset.Status.AVAILABLE,
        "purchase_date": datetime.date(2024, 2, 10),
    },
    {
        "asset_tag": "DEMO-CAM-03",
        "name": "GoPro HERO12 Black Rugged Kit",
        "category": Asset.Category.CAMERA,
        "status": Asset.Status.AVAILABLE,
        "purchase_date": datetime.date(2024, 5, 10),
    },
    {
        "asset_tag": "DEMO-LAP-01",
        "name": "Lenovo ThinkPad P16 Gen 2 Workstation",
        "category": Asset.Category.LAPTOP,
        "status": Asset.Status.AVAILABLE,
        "purchase_date": datetime.date(2024, 3, 1),
    },
    {
        "asset_tag": "DEMO-LAP-02",
        "name": "Apple MacBook Pro 16 M3 Max",
        "category": Asset.Category.LAPTOP,
        "status": Asset.Status.AVAILABLE,
        "purchase_date": datetime.date(2024, 3, 15),
    },
    {
        "asset_tag": "DEMO-SEN-01",
        "name": "Velodyne LiDAR Puck Hi-Res 3D",
        "category": Asset.Category.SENSOR,
        "status": Asset.Status.CHECKED_OUT,
        "purchase_date": datetime.date(2024, 4, 5),
    },
    {
        "asset_tag": "DEMO-SEN-02",
        "name": "FLIR E8 Pro Infrared Thermal Camera",
        "category": Asset.Category.SENSOR,
        "status": Asset.Status.AVAILABLE,
        "purchase_date": datetime.date(2024, 4, 20),
    },
    {
        "asset_tag": "DEMO-SEN-03",
        "name": "Bosch Industrial Vibration Sensor Unit",
        "category": Asset.Category.SENSOR,
        "status": Asset.Status.MAINTENANCE,
        "purchase_date": datetime.date(2024, 5, 15),
    },
    {
        "asset_tag": "DEMO-VEH-01",
        "name": "Ford Ranger 4x4 Survey Truck",
        "category": Asset.Category.VEHICLE,
        "status": Asset.Status.AVAILABLE,
        "purchase_date": datetime.date(2023, 11, 10),
    },
    {
        "asset_tag": "DEMO-VEH-02",
        "name": "Toyota Hilux Inspection Rig",
        "category": Asset.Category.VEHICLE,
        "status": Asset.Status.CHECKED_OUT,
        "purchase_date": datetime.date(2023, 12, 1),
    },
]

DEMO_EMPLOYEES = [
    {
        "employee_code": "DEMO-EMP-01",
        "full_name": "Sarah Chen",
        "email": "sarah.chen@artikate.example.com",
        "is_active": True,
    },
    {
        "employee_code": "DEMO-EMP-02",
        "full_name": "Marcus Rodriguez",
        "email": "marcus.rodriguez@artikate.example.com",
        "is_active": True,
    },
    {
        "employee_code": "DEMO-EMP-03",
        "full_name": "Amina Yusuf",
        "email": "amina.yusuf@artikate.example.com",
        "is_active": True,
    },
    {
        "employee_code": "DEMO-EMP-04",
        "full_name": "Devon Patel",
        "email": "devon.patel@artikate.example.com",
        "is_active": True,
    },
    {
        "employee_code": "DEMO-EMP-05",
        "full_name": "Elena Rostova",
        "email": "elena.rostova@artikate.example.com",
        "is_active": False,  # Required inactive employee
    },
]

# Checkouts definitions with deterministic offset days relative to current time
# Overdue: due_offset_days < 0 and returned_offset_days is None
# Returned on-time: returned_offset_days <= due_offset_days
# Returned late: returned_offset_days > due_offset_days
DEMO_CHECKOUTS = [
    # Overdue open checkout 1 (due 7 days ago, currently open)
    {
        "asset_tag": "DEMO-CAM-01",
        "employee_code": "DEMO-EMP-01",
        "checkout_days_ago": 14,
        "due_days_ago": 7,
        "returned_days_ago": None,
        "asset_status": Asset.Status.CHECKED_OUT,
    },
    # Overdue open checkout 2 (due 3 days ago, currently open)
    {
        "asset_tag": "DEMO-SEN-01",
        "employee_code": "DEMO-EMP-02",
        "checkout_days_ago": 10,
        "due_days_ago": 3,
        "returned_days_ago": None,
        "asset_status": Asset.Status.CHECKED_OUT,
    },
    # Returned on-time checkout 1 (returned 12 days ago, due was 10 days ago -> returned 2 days early)
    {
        "asset_tag": "DEMO-LAP-01",
        "employee_code": "DEMO-EMP-03",
        "checkout_days_ago": 20,
        "due_days_ago": 10,
        "returned_days_ago": 12,
        "asset_status": Asset.Status.AVAILABLE,
    },
    # Returned on-time checkout 2 (returned 15 days ago, due was 15 days ago -> returned on time)
    {
        "asset_tag": "DEMO-VEH-01",
        "employee_code": "DEMO-EMP-01",
        "checkout_days_ago": 25,
        "due_days_ago": 15,
        "returned_days_ago": 15,
        "asset_status": Asset.Status.AVAILABLE,
    },
    # Returned late checkout (returned 16 days ago, due was 20 days ago -> returned 4 days late)
    {
        "asset_tag": "DEMO-LAP-02",
        "employee_code": "DEMO-EMP-02",
        "checkout_days_ago": 30,
        "due_days_ago": 20,
        "returned_days_ago": 16,
        "asset_status": Asset.Status.AVAILABLE,
    },
    # Active on-time open checkout (due 5 days in future, currently open)
    {
        "asset_tag": "DEMO-VEH-02",
        "employee_code": "DEMO-EMP-04",
        "checkout_days_ago": 2,
        "due_days_ago": -5,
        "returned_days_ago": None,
        "asset_status": Asset.Status.CHECKED_OUT,
    },
]


class Command(BaseCommand):
    help = "Deterministically seeds demo assets, employees, and checkouts into the database. Idempotent and safe to run multiple times."

    def handle(self, *args, **options):
        self.stdout.write("Starting deterministic demo data seeding...")
        now = timezone.now().replace(microsecond=0)

        with transaction.atomic():
            # 1. Seed Employees
            emp_created_cnt = 0
            emp_updated_cnt = 0
            employees_by_code = {}
            for emp_data in DEMO_EMPLOYEES:
                emp, created = Employee.objects.update_or_create(
                    employee_code=emp_data["employee_code"],
                    defaults={
                        "full_name": emp_data["full_name"],
                        "email": emp_data["email"],
                        "is_active": emp_data["is_active"],
                    },
                )
                employees_by_code[emp.employee_code] = emp
                if created:
                    emp_created_cnt += 1
                else:
                    emp_updated_cnt += 1

            # 2. Seed Assets
            asset_created_cnt = 0
            asset_updated_cnt = 0
            assets_by_tag = {}
            for asset_data in DEMO_ASSETS:
                asset, created = Asset.objects.update_or_create(
                    asset_tag=asset_data["asset_tag"],
                    defaults={
                        "name": asset_data["name"],
                        "category": asset_data["category"],
                        "status": asset_data["status"],
                        "purchase_date": asset_data["purchase_date"],
                    },
                )
                assets_by_tag[asset.asset_tag] = asset
                if created:
                    asset_created_cnt += 1
                else:
                    asset_updated_cnt += 1

            # 3. Seed CheckOuts (Idempotent lookup by asset & employee)
            checkout_created_cnt = 0
            checkout_updated_cnt = 0
            for co_spec in DEMO_CHECKOUTS:
                asset = assets_by_tag[co_spec["asset_tag"]]
                employee = employees_by_code[co_spec["employee_code"]]

                checked_out_at = now - datetime.timedelta(days=co_spec["checkout_days_ago"])
                due_at = now - datetime.timedelta(days=co_spec["due_days_ago"])
                returned_at = (
                    now - datetime.timedelta(days=co_spec["returned_days_ago"])
                    if co_spec["returned_days_ago"] is not None
                    else None
                )

                # CheckOut has no natural unique constraint, so we match on (asset, employee)
                existing_co = CheckOut.objects.filter(
                    asset=asset,
                    employee=employee,
                ).first()

                if existing_co:
                    existing_co.checked_out_at = checked_out_at
                    existing_co.due_at = due_at
                    existing_co.returned_at = returned_at
                    existing_co.save(update_fields=["checked_out_at", "due_at", "returned_at"])
                    checkout_updated_cnt += 1
                else:
                    co = CheckOut.objects.create(
                        asset=asset,
                        employee=employee,
                        due_at=due_at,
                        returned_at=returned_at,
                    )
                    CheckOut.objects.filter(pk=co.pk).update(checked_out_at=checked_out_at)
                    checkout_created_cnt += 1

                # Ensure asset status is strictly synchronized with its checkout state
                if asset.status != co_spec["asset_status"]:
                    asset.status = co_spec["asset_status"]
                    asset.save(update_fields=["status", "updated_at"])

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded demo data: "
                f"{asset_created_cnt} assets created ({asset_updated_cnt} updated), "
                f"{emp_created_cnt} employees created ({emp_updated_cnt} updated), "
                f"{checkout_created_cnt} checkouts created ({checkout_updated_cnt} updated)."
            )
        )
