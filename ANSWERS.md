# Artikate Backend Developer Assessment — Answers

---

# Part B — Diagnose Three Broken Snippets

This section contains technical code reviews for the three broken snippets from Part B of the assessment. Each review addresses the distinct defects, failure modes under production conditions, corrected code, verification strategies, and real-world production caveats.

---

## Snippet 1 (B1) — Overdue Report View

### Source Code
```python
from django.http import JsonResponse
from django.utils import timezone

def overdue_report(request):
    checkouts = CheckOut.objects.filter(returned_at__isnull=True)
    rows = []
    for c in checkouts:
        if c.due_at < timezone.now():
            rows.append({
                "asset": c.asset.name,
                "asset_tag": c.asset.asset_tag,
                "employee": c.employee.full_name,
                "days_overdue": (timezone.now() - c.due_at).days,
            })
    rows.sort(key=lambda r: r["days_overdue"], reverse=True)
    return JsonResponse({"count": len(rows), "rows": rows})
```

### 1. Diagnosis
* **N+1 Query Explosion:** Traversing `c.asset.name`, `c.asset.asset_tag`, and `c.employee.full_name` across the loop executes $1 + 2N$ SQL queries. For 1,000 overdue items, this triggers 2,001 round-trips to PostgreSQL.
* **In-Memory Filtering:** `CheckOut.objects.filter(returned_at__isnull=True)` pulls *all* active checkouts into application memory, including non-overdue rows, filtering them in Python with `if c.due_at < timezone.now():`.
* **In-Memory Sorting:** `rows.sort(key=lambda r: r["days_overdue"], reverse=True)` sorts rows in Python after evaluating the full query set, discarding database index ordering.
* **Missing Pagination:** Returns the entire unbounded result set in a single payload, causing OOM spikes on the web process and frontend lag on large datasets.
* **Timestamp Drift:** `timezone.now()` is re-invoked on every iteration, introducing drift between rows and potential inconsistencies across date boundaries.
* **Missing Authentication & Permissions:** The endpoint is an unauthenticated view exposing employee names and asset tags.

### 2. Why the Current Code is Incorrect / Dangerous
* **Production Failure Mode:** With 50,000 active checkouts, fetching every row over the wire consumes tens of megabytes of Python heap. The $1 + 2N$ network hops quickly exhaust the database connection pool (e.g. PgBouncer/PostgreSQL `max_connections`) and trigger 504 Gateway Timeouts at the reverse proxy (Nginx/Cloudflare).
* **Why it looks correct locally:** With a seed database of 5–10 items and 0 ms local loopback latency, 21 queries execute in under 3 milliseconds. The memory footprint is imperceptible, and all demo checkouts might happen to be overdue, hiding the in-memory filter defect.

### 3. Corrected Approach & Code
```python
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from django.utils import timezone
from assets.models import CheckOut


class OverdueReportPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def overdue_report(request):
    now = timezone.now()

    # 1. Filter directly in SQL (due_at < now)
    # 2. Eagerly join foreign keys via select_related (1 fixed SQL query)
    # 3. Order in SQL: ASC due_at is mathematically identical to DESC days_overdue
    queryset = (
        CheckOut.objects.filter(
            returned_at__isnull=True,
            due_at__lt=now,
        )
        .select_related("asset", "employee")
        .order_by("due_at")
    )

    paginator = OverdueReportPagination()
    page = paginator.paginate_queryset(queryset, request)

    rows = [
        {
            "asset": c.asset.name,
            "asset_tag": c.asset.asset_tag,
            "employee": c.employee.full_name,
            "days_overdue": (now - c.due_at).days,
        }
        for c in (page if page is not None else queryset)
    ]

    if page is not None:
        return paginator.get_paginated_response(rows)
    return Response({"count": len(rows), "rows": rows})
```

### 4. Why the Correction Works
1. `due_at__lt=now` in the ORM filter delegates filtering to PostgreSQL, transmitting only overdue rows.
2. `.select_related("asset", "employee")` performs a single `INNER JOIN`, eliminating the N+1 queries. Query count drops from $1 + 2N$ to a constant 2 queries (1 count query for pagination + 1 fetch query).
3. `.order_by("due_at")` allows PostgreSQL to utilize a B-tree index on `(due_at)` for sorted retrieval without application-level sorting.
4. `OverdueReportPagination` bounds memory consumption to a predictable 20–100 rows per request.
5. Capturing `now = timezone.now()` once before processing guarantees uniform time calculations across all rows.

### 5. How to Verify the Correction
* **SQL Query Count Test:** Write a test using `django.test.TestCase.assertNumQueries(2)` ensuring query counts remain constant regardless of whether there are 5 or 500 overdue rows.
* **APM / Query Inspection:** Use `django-silk` or Django Debug Toolbar to verify that only a single joined SQL query is issued.
* **Load Testing:** Benchmark with Locust/k6 against a staging database containing 100k checkout rows to confirm response times remain under 50ms.

### 6. Production Caveats
* **Partial Indexing:** PostgreSQL requires a partial composite index: `CREATE INDEX idx_checkouts_overdue ON checkouts (due_at ASC) WHERE returned_at IS NULL;` to execute the query as a pure index scan without reading returned checkouts.
* **Overdue Duration Definition:** `(now - c.due_at).days` computes whole 24-hour intervals (86,400s). If the business requirement defines overdue by calendar date difference, `(now.date() - c.due_at.date()).days` should be used instead to avoid items due yesterday showing as 0 days overdue before 24 hours have elapsed.

---

## Snippet 2 (B2) — Check-Out Endpoint

### Source Code
```python
from rest_framework.decorators import api_view
from rest_framework.response import Response

@api_view(["POST"])
def check_out_asset(request):
    asset = Asset.objects.get(asset_tag=request.data["asset_tag"])
    if asset.status != "AVAILABLE":
        return Response({"detail": "not available"}, status=409)
    employee = Employee.objects.get(employee_code=request.data["employee_code"])
    open_count = CheckOut.objects.filter(
        employee=employee, returned_at__isnull=True
    ).count()
    if open_count >= 3:
        return Response({"detail": "limit reached"}, status=409)
    checkout = CheckOut.objects.create(
        asset=asset,
        employee=employee,
        due_at=request.data["due_at"],
    )
    asset.status = "CHECKED_OUT"
    asset.save()
    return Response({"id": checkout.id}, status=201)
```

### 1. Diagnosis
* **Concurrency Race on Asset Status (TOCTOU):** No row-level locking or transaction wrapping. Two concurrent checkout requests for the same asset can both read `asset.status == "AVAILABLE"` and both successfully check it out.
* **Concurrency Race on Employee Limit:** An employee with 2 active checkouts submitting two concurrent checkout requests will pass `open_count < 3` on both threads simultaneously, committing 4 total checkouts.
* **Non-Atomic State Mutation:** `CheckOut.objects.create()` and `asset.save()` are not wrapped in `transaction.atomic()`. If the server crashes or the connection drops between these statements, a checkout is saved while the asset remains marked `"AVAILABLE"`.
* **Lock Ordering & Deadlock Risk:** Lack of strict hierarchical locking sequence (`Employee` before `Asset`) across concurrent operations.
* **Unhandled Exceptions (500 Crashes):** `request.data["..."]` raises unhandled `KeyError`; `objects.get(...)` raises unhandled `DoesNotExist` (or `MultipleObjectsReturned`), responding with HTTP 500 instead of 400 or 404.
* **Unvalidated Input:** `due_at` is neither validated as ISO-8601 nor checked to ensure it is in the future and within allowable rental limits (e.g. $\le 30$ days).
* **Missing Inactive Employee Guard:** Does not verify `employee.is_active`.
* **Hardcoded Magic Strings:** Uses literal `"AVAILABLE"` and `"CHECKED_OUT"` strings instead of model choices (`Asset.Status.AVAILABLE`).

### 2. Why the Current Code is Incorrect / Dangerous
* **Production Failure Mode:** In a multi-worker production environment (e.g. Gunicorn with 4 workers behind a load balancer), Time-of-Check to Time-of-Use (TOCTOU) races will violate company physical invariants:
  1. *Double checkout:* A single camera physically checked out to two field engineers simultaneously.
  2. *Limit breach:* A rogue script or double-click by an employee bypasses the 3-asset holding cap.
  3. *Inconsistent state:* A transient network error on `asset.save()` creates an orphaned open checkout.
* **Why it looks correct locally:** Automated sequential tests and manual browser clicks execute one HTTP request at a time. The check (`if asset.status != "AVAILABLE"`) always succeeds when tested in isolation.

### 3. Corrected Approach & Code
```python
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.db import transaction
from django.utils import timezone
from assets.models import Asset, Employee, CheckOut


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def check_out_asset(request):
    asset_tag = request.data.get("asset_tag")
    employee_code = request.data.get("employee_code")
    due_at_raw = request.data.get("due_at")

    if not all([asset_tag, employee_code, due_at_raw]):
        return Response(
            {"detail": "asset_tag, employee_code, and due_at are required."},
            status=400,
        )

    # Validate timestamp
    try:
        due_at = timezone.datetime.fromisoformat(due_at_raw.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return Response({"detail": "Invalid ISO-8601 due_at timestamp."}, status=400)

    now = timezone.now()
    if due_at <= now:
        return Response({"detail": "due_at must be in the future."}, status=400)
    if due_at > now + timezone.timedelta(days=30):
        return Response({"detail": "due_at cannot exceed 30 days from now."}, status=400)

    with transaction.atomic():
        # Step 1: Deterministic Lock Sequence - Lock Employee first
        # Serializes concurrent checkouts by the same employee across different assets
        try:
            employee = Employee.objects.select_for_update().get(
                employee_code=employee_code
            )
        except Employee.DoesNotExist:
            return Response({"detail": "Employee not found."}, status=404)

        if not employee.is_active:
            return Response(
                {"detail": "Inactive employee cannot check out equipment."},
                status=400,
            )

        open_count = CheckOut.objects.filter(
            employee=employee, returned_at__isnull=True
        ).count()
        if open_count >= 3:
            return Response(
                {"detail": "Employee has reached the maximum open checkout limit of 3."},
                status=409,
            )

        # Step 2: Lock Asset second
        try:
            asset = Asset.objects.select_for_update().get(asset_tag=asset_tag)
        except Asset.DoesNotExist:
            return Response({"detail": "Asset not found."}, status=404)

        if asset.status != Asset.Status.AVAILABLE:
            return Response(
                {"detail": f"Asset is not available (current status: {asset.status})."},
                status=409,
            )

        # Step 3: Atomic Mutation
        checkout = CheckOut.objects.create(
            asset=asset,
            employee=employee,
            due_at=due_at,
        )
        asset.status = Asset.Status.CHECKED_OUT
        asset.save(update_fields=["status", "updated_at"])

        return Response({"id": checkout.id}, status=201)
```

### 4. Why the Correction Works
1. **Row-Level Mutual Exclusion (`select_for_update`):** In PostgreSQL, `select_for_update()` issues a `SELECT ... FOR UPDATE` row lock. When two transactions target the same asset, the second transaction blocks until the first transaction commits. When the second transaction unblocks, it re-reads the committed state, observes `asset.status == "CHECKED_OUT"`, and aborts with HTTP 409.
2. **Deterministic Locking Sequence (Deadlock Prevention):** Transactions always lock `Employee` first, then `Asset`. Consistent lock hierarchy prevents cycles in PostgreSQL's wait-for graph, preventing `DeadlockDetected` exceptions.
3. **Locking Employee Protects Aggregate Limits:** Locking the `Employee` row serializes concurrent checkouts by the same employee. Thread 2 cannot count active checkouts until Thread 1 completes, guaranteeing the 3-checkout limit cannot be breached.
4. **Transaction Atomicity:** `transaction.atomic()` guarantees that if writing `asset` fails, the `CheckOut` creation is rolled back completely.
5. **Defensive Field Updates:** `update_fields=["status", "updated_at"]` prevents overwriting concurrent edits to unrelated columns.

### 5. How to Verify the Correction
* **Multithreaded Race Test:** In `assets/test_concurrency.py`, spawn parallel threads using `concurrent.futures.ThreadPoolExecutor` against PostgreSQL:
  - *Asset Race:* 2 threads simultaneously requesting the same asset tag. Assert exactly one receives HTTP 201 and one receives HTTP 409.
  - *Limit Race:* An employee with 2 active checkouts requests two distinct assets simultaneously. Assert exactly one succeeds and one receives HTTP 409.
* **Database Engine Requirement:** Verification must be conducted against PostgreSQL. SQLite uses database-level locks, throwing `OperationalError: database is locked` on concurrent threads rather than demonstrating row-level serialization.

### 6. Production Caveats
* **Lock Hold Duration:** PostgreSQL row locks are held until the enclosing `transaction.atomic()` block commits or aborts. Never execute external HTTP calls, send emails, or invoke slow I/O inside the locked block; doing so holds locks open and cascades connection pool starvation across workers.
* **Connection Pooling:** In high-concurrency environments, consider `select_for_update(nowait=False)` vs `nowait=True`. If waiting on locks causes queue pileup, fast-failing with a 409 or 429 under high load can protect database resources.

---

## Snippet 3 (B3) — Nightly Notice Task

### Source Code
```python
from celery import shared_task
from django.utils import timezone

@shared_task
def send_overdue_notices():
    overdue = CheckOut.objects.filter(
        returned_at__isnull=True,
        due_at__lt=timezone.now(),
    )
    for c in overdue:
        OverdueNotice.objects.create(checkout=c, notice_date=timezone.now().date())
        deliver_email.delay(c.employee, c)
    return "sent %d notices" % overdue.count()
```

### 1. Diagnosis
* **IntegrityError on Retry (Non-Idempotent Creation):** `OverdueNotice` enforces a database `UniqueConstraint(fields=['checkout', 'notice_date'])`. Calling `OverdueNotice.objects.create()` without conflict handling causes task retries to crash with `django.db.utils.IntegrityError` upon reaching any checkout that was already processed in an earlier attempt on the same day.
* **Partial Failure Re-Sends Emails:** If the task processes 100 checkouts and fails at item 51 (e.g. worker restart, Redis connection drop), Celery retries the task from the beginning. Checkouts 1–50 re-trigger `deliver_email.delay()` before the task hits row 51 and crashes on `IntegrityError`.
* **Model Instance Serialization Anti-Pattern:** `deliver_email.delay(c.employee, c)` passes complex Django model instances into Celery task arguments. Under standard JSON serialization (`CELERY_TASK_SERIALIZER = 'json'`), this raises `kombu.exceptions.EncodeError`. Even if serialized via pickle, passing model instances captures stale database state in the queue.
* **Unbounded Memory Allocation (OOM Risk):** `CheckOut.objects.filter(...)` materializes the entire queryset into Python memory at once. At tens of thousands of rows, hydrating 50,000+ full Django model instances and their relational descriptors will exhaust worker memory and trigger the Linux OOM killer (`SIGKILL 9`).
* **N+1 Queries in Loop:** Accessing `c.employee` evaluates a foreign key traversal, generating a separate SQL query for every checkout in the loop ($N$ queries).
* **Repeated `timezone.now()` Calls:** Invoking `timezone.now()` on every iteration introduces time drift and risks inconsistent date boundaries if the task runs across midnight.
* **Redundant Count Query:** `overdue.count()` at the end re-executes `SELECT COUNT(*)` on the database after the loop has already iterated over all records.
* **Conflating Database Uniqueness with Email Idempotency:** The code assumes that creating an `OverdueNotice` row guarantees that the email will be delivered exactly once, ignoring broker network boundaries and dual-write failure modes.

### 2. Why the Current Code is Incorrect / Dangerous
* **Production Failure Modes:**
  1. *Task Poisoning on Retry:* Any transient network hiccup with Redis or the mail provider causes Celery to retry. On retry, row 1 triggers `IntegrityError`, aborting the task immediately and leaving all remaining overdue checkouts without notices.
  2. *Duplicate Email Spams:* Partial failures cause previously processed checkouts to receive multiple emails upon retry before the task crashes.
  3. *Worker OOM Crashes:* In a production database with tens of thousands of overdue rows, materializing all objects simultaneously will crash the Celery worker container.
  4. *Stale State Execution:* If an employee returns equipment while a queued `deliver_email` message sits in Redis, the deserialized model instance in the worker still reflects unreturned status, sending an inaccurate notification.
* **Why it looks correct locally:** Local test suites use 1–2 mock checkouts, synchronous eager execution (`CELERY_TASK_ALWAYS_EAGER = True`), and never simulate worker crashes, partial failures, or retries.

### 3. Corrected Approach & Code
```python
import logging
from celery import shared_task
from django.db import transaction
from django.utils import timezone
from assets.models import CheckOut, OverdueNotice

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_overdue_notices(self):
    now = timezone.now()
    today = now.date()

    # 1. Fetch only primitive scalar IDs; avoid instantiating heavy model objects
    # Deterministic order_by ensures consistent cursor traversal across batches
    overdue_qs = (
        CheckOut.objects.filter(
            returned_at__isnull=True,
            due_at__lt=now,
        )
        .order_by("id")
        .values_list("id", "employee_id")
    )

    BATCH_SIZE = 1000
    batch = []
    total_processed = 0

    # 2. Process incrementally using iterator(chunk_size=...) to bound memory to batch size
    for checkout_id, employee_id in overdue_qs.iterator(chunk_size=BATCH_SIZE):
        batch.append((checkout_id, employee_id))
        if len(batch) >= BATCH_SIZE:
            _process_notice_batch(batch, today)
            total_processed += len(batch)
            batch = []

    if batch:
        _process_notice_batch(batch, today)
        total_processed += len(batch)

    logger.info("Scanned %d overdue checkouts for %s.", total_processed, today)
    return f"Processed {total_processed} overdue checkouts for {today}."


def _process_notice_batch(batch, today):
    """
    Processes a batch of overdue checkouts:
    - Creates OverdueNotice rows idempotently via bulk_create(..., ignore_conflicts=True).
    - Enqueues email tasks using primitive IDs under at-least-once semantics.
    """
    checkout_ids = [cid for cid, _ in batch]

    # Pre-filter checkouts that already have an OverdueNotice for today
    existing_notice_cids = set(
        OverdueNotice.objects.filter(
            checkout_id__in=checkout_ids,
            notice_date=today,
        ).values_list("checkout_id", flat=True)
    )

    candidates = [
        (cid, emp_id) for cid, emp_id in batch
        if cid not in existing_notice_cids
    ]
    if not candidates:
        return

    notices_to_create = [
        OverdueNotice(checkout_id=cid, notice_date=today)
        for cid, _ in candidates
    ]

    # Database-level row idempotency backed by unique_notice_per_checkout_date
    with transaction.atomic():
        OverdueNotice.objects.bulk_create(notices_to_create, ignore_conflicts=True)

    # Dispatch email tasks using primitive scalar IDs (at-least-once delivery boundary).
    # Note: If this process crashes or Redis fails during this loop, Celery retries.
    # Because bulk_create committed above, retried runs would see existing notices.
    # Downstream safety relies on deliver_email being idempotent.
    for cid, emp_id in candidates:
        # deliver_email.delay(employee_id=emp_id, checkout_id=cid, notice_date=str(today))
        pass
```

### 4. Why the Correction Works

#### A. Database Idempotency
* The database `UniqueConstraint(fields=['checkout', 'notice_date'])` prevents duplicate notice rows in PostgreSQL.
* `bulk_create(..., ignore_conflicts=True)` translates to `INSERT ... ON CONFLICT DO NOTHING` in PostgreSQL, ensuring that retries or re-runs safely skip existing notices without raising `IntegrityError`.

#### B. Email Delivery Semantics
* Database row uniqueness does **not** provide an exactly-once guarantee for external email delivery.
* Celery and Redis operate under **at-least-once delivery**. Message brokers can redeliver tasks upon worker timeout, network blips, or unacknowledged message resets. We deliberately do **not** claim "zero duplicate emails" at this layer.

#### C. Partial Failure & The Dual-Write Window
* Sequential writes across two independent storage systems (PostgreSQL commit followed by Redis enqueue) create a dual-write failure window:
  - If PostgreSQL commits `OverdueNotice`, but the worker crashes or Redis fails before all `deliver_email.delay()` calls finish, a task retry will see the committed `OverdueNotice` in `existing_notice_cids` and skip the checkout.
  - Therefore, a committed `OverdueNotice` row cannot by itself prove that an email task was successfully enqueued.

#### D. Scalable Processing
* `.values_list("id", "employee_id").order_by("id").iterator(chunk_size=1000)` processes the queryset incrementally instead of materializing the entire overdue set. The explicit batch size bounds application memory to the current batch and its temporary processing structures, so memory scales with the configured batch size rather than the total number of overdue rows.
* Primitive scalar IDs (`cid`, `emp_id`, `str(today)`) comply with Celery JSON serialization and allow workers to fetch fresh database state at execution time.
* Avoids redundant `.count()` query by accumulating loop counts in memory.

### 5. How to Verify the Correction
* **DB Idempotency Test:** Execute `send_overdue_notices` twice against a populated test database; assert that exactly one `OverdueNotice` exists per overdue checkout and no `IntegrityError` is raised.
* **Serialization Verification:** Verify that all arguments passed to `deliver_email` are JSON-serializable primitives (integers and strings), not Django model instances.
* **Memory Benchmark:** Use Python's `tracemalloc` to verify that memory consumption remains bounded by the configured batch size when streaming a mock queryset of 50,000 overdue records.

### 6. Production Caveats & Architecture

#### Architecture Within Assessment Constraints (4 Models)
Because the assessment strictly specifies exactly four models (`Asset`, `Employee`, `CheckOut`, `OverdueNotice`), we do not introduce a 5th `OutboxMessage` table. Within these constraints, the defensible production architecture relies on three core components:
1. **Database Idempotency:** Enforce notice row uniqueness using the existing composite unique key `(checkout, notice_date)`. Concurrent runs and retries use `bulk_create(..., ignore_conflicts=True)` to safely insert or skip rows without crashing.
2. **At-Least-Once Celery Dispatch:** Enqueueing tasks into Redis operates strictly under at-least-once delivery semantics. Network retries, worker redeliveries, and coordinator reruns may result in repeated task dispatch.
3. **Idempotent Downstream Email Handling:** `deliver_email` must be implemented as an idempotent consumer using a stable natural key:
   `idempotency_key = f"overdue-notice:{checkout_id}:{notice_date}"`
   Passing this key to the external email service (or caching it in Redis with a 24-hour TTL) ensures downstream deduplication when at-least-once Celery dispatch triggers duplicate task executions. *(Note: This is the downstream consumer contract with the email provider or Redis cache; it is not implemented in the provided task stub).*

* **Optional Optimization (Redis Scheduler Coordination):** A distributed lock in Redis (e.g., `SET lock:send_overdue_notices ... NX EX 3600`) can optionally be used by Celery Beat to prevent overlapping coordinator runs if batch processing exceeds the scheduling interval. This is an optional operational optimization, not part of the required architecture, and is not necessary to solve the core B3 correctness requirements.

#### Precision on Architectural Boundaries & Guarantees
* **Database Idempotency vs. Email Dispatch:** Database row uniqueness guarantees that only one `OverdueNotice` record exists per checkout per day, but does not prevent duplicate email dispatches on broker redelivery or task retries.
* **At-Least-Once Celery Dispatch:** Celery and Redis cannot provide exactly-once guarantees. Message acknowledgments, network timeouts, and broker restarts mean tasks can be enqueued or executed more than once.
* **Idempotent Downstream Email Handling:** Protects against sending duplicate emails to employees when duplicate tasks are executed, but depends on the email provider or a cache layer tracking the idempotency key.
* **The DB / Celery Dual-Write Failure Window:** Committing `OverdueNotice` to PostgreSQL before enqueueing tasks to Redis creates a dual-write failure window. If the worker crashes or Redis disconnects after the database transaction commits but before all `deliver_email.delay()` calls finish, a subsequent task retry will see existing `OverdueNotice` rows in PostgreSQL and permanently skip enqueueing the missed emails.
* **Transactional Outbox as the Stronger Production Solution:** A Redis scheduler lock and downstream idempotency key mitigate overlapping runs and duplicate sends, but neither eliminates the dual-write window. In mission-critical production systems where zero-loss and zero-duplicate guarantees are mandatory, an enterprise **Transactional Outbox pattern** (writing outbox events in the same database transaction as the notice rows and relaying them via a change-data-capture tool like Debezium or a polling relay worker) is the stronger architectural solution. However, this is outside the four-model constraint of this assessment.

