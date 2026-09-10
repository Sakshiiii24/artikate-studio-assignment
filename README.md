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
- **Background Worker & Broker:** Celery & Redis *(to be wired in subsequent stage)*
- **Testing:** `pytest` & `pytest-django`
- **Containerisation:** Docker & Docker Compose *(to be wired in subsequent stage)*

---

## 3. Current Setup Instructions (Incremental Stage 1)

### Prerequisites
- Python 3.12+ installed
- Git installed
- PostgreSQL 15+ (or Docker for running database containers)

### 1. Clone & Navigate to Repository
```bash
git clone <repo-url>
cd "Artikate Studio"
```

### 2. Set Up Virtual Environment
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Copy the template configuration file:
```bash
cp .env.example .env
```
Edit `.env` if needed to point to your target PostgreSQL database:
```ini
DEBUG=True
SECRET_KEY=django-insecure-development-secret-key-change-in-production
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/artikate_db
ALLOWED_HOSTS=localhost,127.0.0.1,0.0.0.0
```

### 5. Run Django System Checks
Verify system configuration and dependencies:
```bash
python manage.py check
```

### 5. Run Database Migrations
Apply initial database schema:
```bash
python manage.py migrate
```

### 6. Run Tests
Verify system tests via pytest:
```bash
pytest
```

### 7. Start the Development Server
```bash
python manage.py runserver
```

### 8. Verify Health Endpoint
Check that the service is running and reporting database connectivity:
```bash
curl -s http://127.0.0.1:8000/health/
# or
curl -s http://127.0.0.1:8000/api/v1/health/
```

Expected JSON response when database is connected:
```json
{
  "status": "healthy",
  "database": {
    "connected": true,
    "engine": "django.db.backends.postgresql"
  }
}
```

---

## 4. Assumptions

1. **Incremental Repository Evolution:** In adherence to the assessment grading criteria ("commit history is graded"), the project is bootstrapped cleanly without committing unreviewed business logic or monolithic boilerplate upfront.
2. **Project Architecture:** The project uses `config` as the central project package containing settings and root URL routing, with `assets` serving as the main domain application.
3. **PostgreSQL Driver:** Configured to use modern `psycopg` (v3) via `psycopg[binary]`, which is fully supported by Django 5.x and handles high-throughput asynchronous/synchronous operations natively.
4. **Timezone Awareness:** Set `USE_TZ = True` with default `TIME_ZONE = 'UTC'` to guarantee timezone-aware timestamps across all models, overdue calculations, and background tasks.
5. **Health Check Routing:** Mounted at both `/health/` and `/api/v1/health/` so monitoring systems can probe either standard endpoint without authentication.
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

---

## 5. Known Gaps

1. **Domain Models & Migrations:** Completed in Stage 2 (`assets/models.py` and migration `0001_initial.py`).
2. **Checkout & Return Business Logic:** Completed in Stage 3 (`POST /api/v1/checkouts/` and `POST /api/v1/checkouts/{id}/return/` with database row-level locking).
3. **Asset Management & Reporting Endpoints:** Completed in Stage 4 (`/api/v1/assets/`, `/api/v1/assets/{id}/`, `/api/v1/employees/{code}/summary/`, and `/api/v1/reports/overdue/`).
4. **Seed Data Command:** `python manage.py seed_demo_data` to be implemented in upcoming stage.
5. **Celery & Redis:** Asynchronous background task `flag_overdue_checkouts` and Celery Beat scheduler are not yet wired up.
6. **Docker Stack:** `Dockerfile` and `docker-compose.yml` defining the four services (Django, PostgreSQL, Redis, Celery) will be added in the containerisation phase.
