# Field Asset Check-Out Service

Backend developer assessment service for **Artikate Private Limited**.

---

## 1. Project Purpose

The **Field Asset Check-Out Service** is an internal REST API designed to track physical field equipment (cameras, laptops, sensors, and vehicles) checked out to and returned by field employees. The service ensures:
- Strict tracking of asset status and employee availability.
- Concurrency-safe check-out reservations at the database level.
- Enforcement of business constraints (maximum 3 active check-outs per employee, check-out duration limits up to 30 days).
- Real-time aggregation of employee hold statistics in a single database query.
- Automated identification and reporting of overdue assets via scheduled background workers.

---

## 2. Technology Stack

- **Language:** Python 3.12+ (compatible with Python 3.14)
- **Web Framework:** Django 5.1
- **API Framework:** Django REST Framework (DRF) 3.15
- **Database:** PostgreSQL (with `psycopg` v3 driver)
- **Configuration:** 12-Factor app architecture via `django-environ`
- **Background Worker & Broker:** Celery & Redis (Celery worker configured in Docker stack; overdue task in Stage 6)
- **Testing:** `pytest` & `pytest-django`
- **Containerisation:** Docker & Docker Compose

---

## 3. Docker & Local Setup Instructions

### Prerequisites
- Docker & Docker Compose v2+ installed
- (Optional for host-local testing): Python 3.12+, PostgreSQL 15+

---

### Method A: Running with Docker Compose (Recommended)

#### 1. Build and Start the Services
Start the 4 core services (`web`, `db` [PostgreSQL 15], `redis`, `worker` [Celery worker with embedded Beat scheduler]):
```bash
docker compose up --build -d
```
Verify container status and health:
```bash
docker compose ps
```

#### 2. Apply Database Migrations
Run schema migrations inside the `web` container:
```bash
docker compose exec web python manage.py migrate
```

#### 3. Seed Deterministic Demo Data
Run the deterministic and idempotent demo seed command:
```bash
docker compose exec web python manage.py seed_demo_data
```
> [!NOTE]
> The seed command is idempotent and safe to run multiple times. It provisions at least 8 assets (across all 4 categories), 5 employees (including 1 inactive), 2 currently overdue open checkouts, 2 returned on-time checkouts, and 1 returned-late checkout, synchronizing asset statuses.

#### 4. Create an Authentication User / Superuser
To access authenticated endpoints or Django admin:
```bash
docker compose exec -it web python manage.py createsuperuser
```

#### 5. Verify Health Endpoint
```bash
curl -s http://127.0.0.1:8000/health/
# or
curl -s http://127.0.0.1:8000/api/v1/health/
```

#### 6. Run Test Suite Inside Container (Against PostgreSQL)
Run all tests including concurrency tests against live PostgreSQL:
```bash
docker compose exec web pytest -v
```

---

### Method B: Host Local Setup (Development & Lightweight Testing)

#### 1. Set Up Virtual Environment & Dependencies
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

#### 2. Database Requirements & Concurrency Verification
- **Runtime Database:** PostgreSQL 15+ is the **required runtime database** for production and container execution.
- **Lightweight Local Tests:** SQLite can be used for rapid local test iterations (`DATABASE_URL=sqlite:///db.sqlite3 pytest`).
- **PostgreSQL Concurrency Requirement:** PostgreSQL is **strictly required** for concurrency verification (`assets/test_concurrency.py`). True row-level locking (`SELECT FOR UPDATE` on `Employee` and `Asset`) requires PostgreSQL MVCC. Under SQLite, file-level locks raise `OperationalError: database is locked` on concurrent threads, and concurrency tests will automatically skip under SQLite.

To run tests against PostgreSQL from the host (with Docker PostgreSQL running on port 5432):
```bash
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/artikate_db pytest -v
```

---

## 4. Assumptions

1. **Incremental Repository Evolution:** In adherence to the assessment grading criteria ("commit history is graded"), the project is bootstrapped cleanly without committing unreviewed business logic or monolithic boilerplate upfront.
2. **Project Architecture:** The project uses `config` as the central project package containing settings, Celery base configuration, and root URL routing, with `assets` serving as the main domain application.
3. **PostgreSQL Driver:** Configured to use modern `psycopg` (v3) via `psycopg[binary]`, which is fully supported by Django 5.x and handles high-throughput asynchronous/synchronous operations natively.
4. **Timezone Awareness:** Set `USE_TZ = True` with default `TIME_ZONE = 'UTC'` to guarantee timezone-aware timestamps across all models, overdue calculations, and background tasks.
5. **Health Check Routing:** Mounted at both `/health/` and `/api/v1/health/` so monitoring systems and Docker container healthchecks can probe either standard endpoint without authentication.
6. **Environment Separation:** Configuration follows 12-factor principles using `django-environ`, allowing seamless transitions between local development, testing, and Docker container environments.
7. **Unique Fields & Indexing:** The specification states `asset_tag` and `employee_code` are unique and indexed. In PostgreSQL and Django, setting `unique=True` on a field inherently creates a unique B-tree index. Specifying `db_index=True` on a unique field is redundant, so `unique=True` was used without an unnecessary duplicate index definition.
8. **Explicit Database Table Names:** Specified `db_table = 'assets'`, `db_table = 'employees'`, `db_table = 'checkouts'`, and `db_table = 'overdue_notices'` in model `Meta` to match the exact table schema defined in Part C of the assessment specification.
9. **Notice Uniqueness Constraint:** Enforced via `models.UniqueConstraint(fields=['checkout', 'notice_date'], name='unique_notice_per_checkout_date')` in `Meta.constraints` (modern Django practice preferred over deprecated `unique_together`).
10. **Authentication Mechanism:** Configured DRF's `BasicAuthentication` and `SessionAuthentication` with `IsAuthenticated` permission class. This provides standards-compliant HTTP authentication without requiring extraneous third-party token migrations at this stage, while `/health/` remains explicitly unauthenticated via `@permission_classes([AllowAny])`.
11. **Concurrency and Lock Ordering:** To completely prevent deadlocks while ensuring 100% database-level isolation:
    - Checkout transactions always acquire locks in the strict hierarchical sequence: `Employee` row first, then `Asset` row (`select_for_update()`).
    - Locking `Employee` row first is mandatory for the 3-checkout limit: it serializes concurrent checkouts by the same employee across different assets. Without this lock, two simultaneous transactions would both read `open_count = 2`, both pass validation, and commit 4 total open checkouts.
    - Return transactions lock the `CheckOut` row first, verify it has not been returned, and then lock the associated `Asset` row, updating both within `transaction.atomic()`.
12. **Employee Summary Single-Query Aggregation:** The four employee summary metrics (`lifetime_checkout_count`, `currently_held`, `currently_overdue`, and `mean_hold_duration_days`) are resolved entirely inside the database engine in a single query via `Employee.objects.filter(...).annotate(...)` using conditional SQL `FILTER (WHERE ...)` clauses (`Count(filter=...)` and `Avg(filter=...)`). Python only formats the resulting database-computed timedelta into days without loading individual checkout rows.
13. **N+1 Prevention in Reports & Detail Views:**
    - `GET /api/v1/reports/overdue/` uses `.select_related('asset', 'employee')` to eagerly join related models, executing a fixed 2 queries (1 count for pagination + 1 data fetch) regardless of the number of rows.
    - `GET /api/v1/assets/{id}/` prefetches active open checkouts with their related employee (`prefetch_related(Prefetch(...))`) to resolve `current_holder` without additional database queries.
14. **Overdue Ordering Definition:** "Most overdue first" corresponds to `order_by('due_at')` (ASC), placing the earliest past-due checkouts (those overdue for the greatest number of days) at the top of the report.
15. **Deterministic Seed Idempotency:** Because `CheckOut` has no natural unique constraint, the `seed_demo_data` command uses deterministic asset tags and matches existing checkouts by `(asset, employee)` within an atomic transaction. Re-running the command updates timestamps to keep overdue/return statuses current without inflating record counts.

16. **Celery Beat Hourly Schedule & Notice Idempotency:** Celery Beat is scheduled to trigger `assets.tasks.flag_overdue_checkouts` at the top of every hour (`crontab(minute=0)`), run directly within the Celery worker container using embedded Beat (`celery -A config worker -B`). The task scans for open checkouts with `due_at < timezone.now()`. Idempotency is enforced by pre-filtering checkouts that already have an `OverdueNotice` for today's date (`timezone.now().date()`) and performing a bulk insertion with `ignore_conflicts=True`. The database unique constraint `unique_notice_per_checkout_date` on `(checkout, notice_date)` acts as the ultimate concurrency guarantee: if multiple worker processes execute or race simultaneously, duplicate inserts are safely ignored without errors and exactly one notice is recorded per checkout per calendar day.

---

## 5. Known Gaps

1. **Domain Models & Migrations:** Completed in Stage 2 (`assets/models.py` and migration `0001_initial.py`).
2. **Checkout & Return Business Logic:** Completed in Stage 3 (`POST /api/v1/checkouts/` and `POST /api/v1/checkouts/{id}/return/` with database row-level locking).
3. **Asset Management & Reporting Endpoints:** Completed in Stage 4 (`/api/v1/assets/`, `/api/v1/assets/{id}/`, `/api/v1/employees/{code}/summary/`, and `/api/v1/reports/overdue/`).
4. **Seed Data Command:** Completed in Stage 5 (`python manage.py seed_demo_data`).
5. **Docker Container Stack:** Completed in Stage 5 (`Dockerfile` and `docker-compose.yml` with `web`, `db`, `redis`, `worker`).
6. **Celery Overdue Task & Beat:** Completed in Stage 6 (`assets/tasks.py` with `flag_overdue_checkouts` and embedded Celery Beat (`-B`) in the worker service, maintaining exactly 4 container services in `docker-compose.yml`).
7. **Worker-Unavailable & Beat Scheduling Failure Mode (Known Gap):**
   - **Queue Accumulation During Same-Day Worker Downtime:** If the Celery worker container is stopped, crashing, or backlogged while Celery Beat continues running, Beat continues enqueuing hourly `flag_overdue_checkouts` task messages into the Redis broker. When the worker resumes, it processes the backlog. Because `flag_overdue_checkouts` is protected by the database unique constraint on `(checkout, notice_date)` with `ignore_conflicts=True`, the first task execution creates the notices for the day, and all duplicate runs safely create 0 additional notices without error.
   - **Multi-Day Worker Outage Gap:** The task stamps notices dynamically using the execution day (`timezone.now().date()`). If the worker service remains down across a calendar day boundary (spanning 24+ hours), no notices are generated for the missed days retroactively. Once restored, the worker will only generate notices stamped with the current calendar date of execution. In a production system, this gap would be resolved by implementing a historical date-range reconciliation / catch-up audit or persisting the last-checked watermark timestamp.
