# Field Asset Check-Out Service

Internal REST API for tracking physical field equipment (cameras, laptops, sensors, and vehicles) checked out to and returned by field employees. Built with Django, DRF, PostgreSQL, Redis, and Celery.

---

## Tech Stack

- **Backend:** Python 3.12, Django 5.1, Django REST Framework 3.15
- **Database:** PostgreSQL 15 with `psycopg` (v3)
- **Queue / Cache:** Celery with Redis broker and Beat scheduler
- **Testing:** pytest, pytest-django
- **Infra:** Docker & Docker Compose

---

## Quickstart (Docker)

The fastest way to spin up the entire stack (web app, PostgreSQL, Redis, and Celery worker with embedded Beat):

```bash
# 1. Start all 4 services
docker compose up --build -d

# 2. Run database migrations
docker compose exec web python manage.py migrate

# 3. Seed deterministic demo data
docker compose exec web python manage.py seed_demo_data

# 4. Run test suite against live PostgreSQL
docker compose exec web pytest -v
```

The API will be available at `http://localhost:8000/`.

### Health Check
```bash
curl -s http://localhost:8000/health/
# returns: {"status": "ok", "timestamp": "..."}
```

### Admin Access
To log into Django admin or test authenticated endpoints:
```bash
docker compose exec -it web python manage.py createsuperuser
```

---

## Local Development (Without Docker)

If you prefer running tests or development directly on your host machine:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# Run against local PostgreSQL (required for concurrency tests):
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/artikate_db pytest -v

# Or rapid SQLite tests (concurrency tests automatically skip under SQLite):
pytest -v
```

---

## Key Architecture & Design Choices

### 1. Concurrency & Deadlock Prevention
When an employee checks out an asset, two constraints must be enforced atomically:
1. The asset must currently be available.
2. The employee cannot exceed the 3-active-checkout limit.

To prevent race conditions without deadlocks, transactions always acquire pessimistic row locks in a strict hierarchical order:
- **Lock `Employee` first (`select_for_update()`)** $\to$ serializes concurrent checkout requests by the same person across multiple browser tabs/threads.
- **Lock `Asset` second (`select_for_update()`)** $\to$ prevents two different employees from checking out the exact same equipment simultaneously.

For return requests, the `CheckOut` record is locked first, followed by the associated `Asset`, ensuring status flips are atomic.

### 2. Single-Query Statistics Aggregation
The employee summary endpoint (`/api/v1/employees/{code}/summary/`) calculates:
- Lifetime check-out count
- Currently held assets
- Currently overdue assets
- Average hold duration (days)

All 4 metrics are computed inside PostgreSQL in a single database query using Django ORM's `.annotate()` with SQL `FILTER (WHERE ...)` clauses (`Count` and `Avg`). No checkout rows are loaded into Python memory.

### 3. Celery Overdue Task & Notice Idempotency
An hourly background task (`assets.tasks.flag_overdue_checkouts`) scans for active checkouts where `due_at < now()`.
- Idempotency is enforced at the database level with a unique constraint on `(checkout, notice_date)`.
- The task bulk-inserts today's notices with `ignore_conflicts=True`. Even if multiple worker processes run concurrently or retry on network failure, duplicate notices for the same calendar day are cleanly ignored.

---

## Assumptions

1. **Table Names:** Model `Meta` explicitly sets `db_table` to `assets`, `employees`, `checkouts`, and `overdue_notices` to match the exact schema specifications.
2. **Indexing:** `asset_tag` and `employee_code` use `unique=True`, which creates a B-tree index automatically in PostgreSQL. No duplicate `db_index=True` was added.
3. **Timezones:** All timestamps are timezone-aware (`USE_TZ = True`, UTC default) to prevent timezone drift across database queries and Celery tasks.
4. **Authentication:** Using Django's built-in `SessionAuthentication` and `BasicAuthentication` with `IsAuthenticated` on business endpoints, keeping `/health/` open via `AllowAny`.
5. **Overdue Ordering:** "Most overdue first" is implemented as `order_by('due_at')` ascending, putting the oldest past-due dates at the top.
6. **Seed Idempotency:** Running `python manage.py seed_demo_data` multiple times is safe; it refreshes timestamps for existing records rather than creating duplicate entries.

---

## Known Limitations & Edge Cases

- **Multi-Day Worker Outage:** The hourly overdue task stamps notices with today's date (`now().date()`). If the worker service were down continuously across midnight, it does not backfill missed intermediate calendar dates retroactively upon reboot; it resumes generating notices for the active date. A production enhancement would be an audit watermark table to backfill missing date ranges.
- **SQLite Concurrency Limitation:** Concurrency tests require PostgreSQL row-level locks (`SELECT FOR UPDATE`). SQLite table-level locks raise `database is locked` under threaded access, so the concurrency suite skips itself if run against SQLite.

---

## Walkthrough Video

- **Link:** [Artikate Assessment Walkthrough (Google Drive)](https://drive.google.com/file/d/1dcSgSxU3q72llo1D3QIlvYsmOY4FDD-n/view?usp=drivesdk)
- **Duration:** ~6 minutes
- **Agenda:** Stack startup via Docker, migration + seed demo, API endpoint walkthrough (checkout, summary stats, overdue report), test suite execution, and technical narration of concurrency locking decisions.
