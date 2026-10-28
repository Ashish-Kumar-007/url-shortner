# URL Shortener

A modular-monolith URL shortener using FastAPI, SQLAlchemy, and PostgreSQL.
The application stores URL mappings durably, generates seven-character short
codes, redirects with HTTP 302, enforces optional expiration, and reports click
counts.

## Phase 1 — MVP (complete)

### Included

- `POST /api/shorten` creates a short link. The same destination can be shortened
  more than once and receives a distinct code.
- `GET /{short_code}` redirects to the destination with HTTP 302 and atomically
  increments the click count. Unknown and expired codes return 404.
- `GET /api/urls/{short_code}/stats` returns the destination, timestamps,
  expiration, and public click count.
- `GET /health` checks database connectivity.
- PostgreSQL is the default database. SQLAlchemy creates the `urls` table at
  application startup; mappings and counts are stored durably.
- Codes use Python's cryptographically secure `secrets` module. Unique-key
  collisions are retried a bounded number of times.
- Request validation limits destinations to HTTP(S) URLs of at most 2,048
  characters and requires expiration timestamps to include a timezone and be in
  the future.
- Tests use SQLite so the suite can run without a local PostgreSQL server or
  Docker. Set `DATABASE_URL` to PostgreSQL to run the service against PostgreSQL.

### Prerequisites

- Python 3.11 or newer
- PostgreSQL 14 or newer for the default application database
- `uv` (or Python's built-in `venv` and `pip`)

### Setup

```powershell
uv sync
Copy-Item .env.example .env
```

Set `DATABASE_URL` in `.env` to a PostgreSQL connection string for a database
that exists and is reachable. The default is
`postgresql+psycopg://postgres:postgres@localhost:5432/url_shortener`.
`BASE_URL` controls the absolute short URL returned by the create endpoint.

Run the app and tests:

```powershell
uv run uvicorn app.main:app --reload
uv run pytest
```

The app creates the `urls` table on startup. Visit
`http://localhost:8000/docs` for the interactive API documentation.

### API examples

Create a link:

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://localhost:8000/api/shorten `
  -ContentType 'application/json' `
  -Body '{"target_url":"https://example.com/docs","expires_at":"2030-01-01T00:00:00Z"}'
```

Example response:

```json
{
  "short_code": "aB3dE9x",
  "short_url": "http://localhost:8000/aB3dE9x",
  "target_url": "https://example.com/docs",
  "expires_at": "2030-01-01T00:00:00Z"
}
```

Open the returned `short_url` to follow its HTTP 302 redirect. Retrieve
statistics with `GET /api/urls/{short_code}/stats`; a successful redirect
increments `click_count`.

### Verification

Automated tests cover link creation, distinct codes for repeated URLs,
redirects, click counts, expiration and unknown codes, URL validation,
persistence across app restarts, and concurrent creation.

## Phase 2 — Reliability and correctness (planned)

Define explicit transaction boundaries and database timeouts; exercise
concurrent creation and click updates against PostgreSQL; standardize dependency
failure responses; and add structured logs and basic request metrics.

## Phase 3 — Performance optimization (planned)

Establish endpoint-specific benchmarks for throughput, latency, errors,
resource use, and database connections. Inspect query plans, tune pooling, and
measure click-counter contention before deciding whether Redis or a different
analytics design is justified. Test at 25, 50, and 100 requests per second and
then find the system's breaking point.

## Phase 4 — Scale the architecture (planned)

Scale out stateless app instances behind a load balancer; introduce Redis only
with correct cache expiration; separate click analytics into an asynchronous
queue and worker if write contention warrants it; evaluate database scaling
based on measurements; and document health checks, graceful shutdown, and
recovery behavior.

## Phase 5 — Production readiness (planned)

Add rate limiting and abuse monitoring, safe redirect policies, managed
secrets and TLS, operational metrics and alerts, database backup and restore
tests, CI/CD and staged deployment, capacity and cost estimates, and incident
and rollback runbooks. Document deployment and recovery objectives.
