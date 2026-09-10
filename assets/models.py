from django.db import models


class Asset(models.Model):
    """
    Physical equipment available for checkout by field employees.
    """
    class Category(models.TextChoices):
        CAMERA = 'CAMERA', 'Camera'
        LAPTOP = 'LAPTOP', 'Laptop'
        SENSOR = 'SENSOR', 'Sensor'
        VEHICLE = 'VEHICLE', 'Vehicle'

    class Status(models.TextChoices):
        AVAILABLE = 'AVAILABLE', 'Available'
        CHECKED_OUT = 'CHECKED_OUT', 'Checked Out'
        MAINTENANCE = 'MAINTENANCE', 'Maintenance'

    asset_tag = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=16, choices=Category.choices)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.AVAILABLE,
    )
    purchase_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'assets'

    def __str__(self):
        return f"{self.name} ({self.asset_tag})"


class Employee(models.Model):
    """
    Company personnel eligible to check out field equipment.
    """
    employee_code = models.CharField(max_length=16, unique=True)
    full_name = models.CharField(max_length=120)
    email = models.EmailField(unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'employees'

    def __str__(self):
        return f"{self.full_name} ({self.employee_code})"


class CheckOut(models.Model):
    """
    Record of an asset checked out to an employee.
    """
    asset = models.ForeignKey(
        Asset,
        on_delete=models.PROTECT,
        related_name='checkouts',
    )
    employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name='checkouts',
    )
    checked_out_at = models.DateTimeField(auto_now_add=True)
    due_at = models.DateTimeField()
    returned_at = models.DateTimeField(null=True, blank=True)
    condition_note = models.TextField(blank=True)

    class Meta:
        db_table = 'checkouts'

    def __str__(self):
        return f"CheckOut: {self.asset.asset_tag} -> {self.employee.employee_code}"


class OverdueNotice(models.Model):
    """
    Notice recorded when an active checkout exceeds its due date.
    """
    checkout = models.ForeignKey(
        CheckOut,
        on_delete=models.CASCADE,
        related_name='notices',
    )
    notice_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'overdue_notices'
        constraints = [
            models.UniqueConstraint(
                fields=['checkout', 'notice_date'],
                name='unique_notice_per_checkout_date',
            )
        ]

    def __str__(self):
        return f"Notice for CheckOut #{self.checkout_id} on {self.notice_date}"
