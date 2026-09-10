import logging
from celery import shared_task
from django.db import transaction
from django.utils import timezone

from assets.models import CheckOut, OverdueNotice

logger = logging.getLogger(__name__)


@shared_task(name="assets.tasks.flag_overdue_checkouts")
def flag_overdue_checkouts():
    """
    Periodic background task that scans for currently open checkouts
    exceeding their due date and creates an OverdueNotice for today's date.

    Correctness & Idempotency:
    - Finds all open checkouts: returned_at IS NULL and due_at < timezone.now().
    - Records at most one notice per checkout per calendar day.
    - Uses bulk_create with ignore_conflicts=True backed by the database
      UniqueConstraint('checkout', 'notice_date') as the final correctness
      guarantee against concurrent worker executions.
    - Returns a summary dictionary including the count of newly created notices.
    """
    now = timezone.now()
    today = now.date()

    # Query all currently open checkouts whose due date is in the past
    overdue_checkout_ids = list(
        CheckOut.objects.filter(
            returned_at__isnull=True,
            due_at__lt=now,
        ).values_list("id", flat=True)
    )

    if not overdue_checkout_ids:
        logger.info("flag_overdue_checkouts: No overdue open checkouts found at %s.", now.isoformat())
        return {
            "status": "success",
            "overdue_count": 0,
            "notices_created": 0,
            "notice_date": str(today),
        }

    # Identify checkouts that already have a notice recorded for today
    existing_notice_checkout_ids = set(
        OverdueNotice.objects.filter(
            checkout_id__in=overdue_checkout_ids,
            notice_date=today,
        ).values_list("checkout_id", flat=True)
    )

    # Filter down to checkouts requiring a new notice today
    pending_checkout_ids = [
        cid for cid in overdue_checkout_ids
        if cid not in existing_notice_checkout_ids
    ]

    notices_to_create = [
        OverdueNotice(checkout_id=cid, notice_date=today)
        for cid in pending_checkout_ids
    ]

    created_count = 0
    if notices_to_create:
        # Atomic block with database-level constraint conflict handling
        with transaction.atomic():
            before_count = OverdueNotice.objects.filter(
                checkout_id__in=pending_checkout_ids,
                notice_date=today,
            ).count()

            OverdueNotice.objects.bulk_create(
                notices_to_create,
                ignore_conflicts=True,
            )

            after_count = OverdueNotice.objects.filter(
                checkout_id__in=pending_checkout_ids,
                notice_date=today,
            ).count()

            created_count = after_count - before_count

    logger.info(
        "flag_overdue_checkouts: %d overdue checkouts scanned; %d notices created for %s.",
        len(overdue_checkout_ids),
        created_count,
        today,
    )

    return {
        "status": "success",
        "overdue_count": len(overdue_checkout_ids),
        "notices_created": created_count,
        "notice_date": str(today),
    }
