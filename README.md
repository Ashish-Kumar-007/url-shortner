# URL Shortener

An open-source URL shortener built with FastAPI, PostgreSQL, and SQLAlchemy.
Create secure seven-character short links, optionally expire them, redirect
with HTTP 302, and inspect click statistics through an API or a lightweight web
page.

## Phase 1 — MVP (complete)

- `POST /api/shorten` creates a unique short link, even when the destination URL
  has already been shortened.
- `GET /{short_code}` redirects to the original HTTP(S) URL and atomically
  records a click. Unknown and expired codes return 404.
- `GET /api/urls/{short_code}/stats` returns public click statistics and link
  timestamps.
- `GET /health` checks database readiness; `/health/live` is a liveness probe.
- PostgreSQL stores links and click events durably. SQLite is also supported
  for lightweight local tests.
- URL length and scheme and timezone-aware future expirations are validated.

## Phase 2 — Reliability and correctness (complete)

- Link creation, redirect click events, and batched click aggregation use
  explicit SQLAlchemy transaction boundaries.
- Short-code collisions receive bounded retries. Database connect, statement,
  and pool timeouts are configurable.
- API errors have a consistent JSON envelope; database failures return 503.
- JSON request logs include request IDs and duration. Prometheus request counts
  and latency histograms are exposed at `/metrics/`.
- `/health/ready` checks PostgreSQL; `/health/live` does not depend on it.
- The test suite runs against SQLite locally and PostgreSQL in GitHub Actions.
  Concurrency tests exercise code creation, click-event processing, and
  consistent final click counts.

## Phase 3 — Performance optimization (benchmarking available)

Run the API locally, then measure link creation, redirects (repeated clicks on
one code), and statistics separately at 25, 50, and 100 requests per second:

```powershell
uv run python benchmarks/run.py --base-url http://localhost:8000 --rates 25 50 100 --duration 30
```

The script reports achieved requests/second, errors, client-side skipped
requests, and p50/p95/p99 latency. Each endpoint/rate combination schedules
requests for the requested duration, then waits for in-flight work to finish.
Scrape `/metrics/` for request totals and latency histograms; use `docker stats`
for container CPU/memory and PostgreSQL's
`pg_stat_activity` for active connections. Compare runs on the same machine
and database before drawing conclusions.

### Initial local baseline

Measured 2026-10-28 on the local Windows/Docker Desktop setup, against
PostgreSQL 16, before asynchronous click aggregation. Each cell ran for 10
seconds; all requests succeeded and the client skipped none. These short runs
are a directional baseline, not a production capacity claim.

| Endpoint | Target RPS | Achieved RPS | p50 ms | p95 ms | p99 ms |
|---|---:|---:|---:|---:|---:|
| Create | 25 | 24.94 | 54.51 | 57.74 | 59.27 |
| Redirect | 25 | 24.99 | 12.49 | 19.26 | 198.66 |
| Stats | 25 | 25.00 | 9.79 | 12.74 | 14.86 |
| Create | 50 | 49.81 | 54.08 | 59.15 | 60.98 |
| Redirect | 50 | 49.99 | 12.74 | 24.17 | 162.02 |
| Stats | 50 | 49.98 | 9.74 | 14.89 | 17.77 |
| Create | 100 | 97.40 | 58.91 | 349.80 | 403.58 |
| Redirect | 100 | 99.93 | 13.09 | 103.57 | 208.12 |
| Stats | 100 | 99.62 | 9.95 | 19.64 | 39.29 |

During sampling, the app peaked at 118% CPU and PostgreSQL at 23.09%; the
highest observed database client connection count was 16. The redirect tail
latency rose sharply at 100 RPS while stats stayed lower, so click-counter
contention warranted a post-baseline experiment. A mapping cache alone would
not remove that write.

The short-code lookup plan uses the `urls_pkey` index:

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT short_code, target_url, expires_at
FROM urls
WHERE short_code = 'aB3dE9x';
```

In the sampled run, PostgreSQL selected an index scan with one shared-buffer
hit.

### Post-baseline click aggregation

Redirects now persist a click event and return without updating the shared URL
counter row. The separate PostgreSQL-backed worker claims event batches with
`FOR UPDATE SKIP LOCKED`, aggregates counts per code, and commits the counter
updates and event deletion together. This avoids lost increments across
workers and retries a batch after a worker crash. Click statistics are
eventually consistent; the Compose worker polls every 250 ms by default.

Repeat the benchmark above to compare the redirect tail latency before and
after this change. Redis remains deferred: the test measured a write bottleneck,
and mapping caching does not address it.

The same 10-second run after adding the worker reduced redirect p95 at 100 RPS
from 103.57 ms to 62.20 ms and p99 from 208.12 ms to 124.39 ms, with no request
errors or client skips. At 25/50 RPS, redirect p95 was 14.24/14.18 ms. During
the post-change run, app CPU peaked at 99.66%, worker CPU at 13.7%, database CPU
at 28.86%, and the maximum PostgreSQL client connection count was 8.

Five-second exploratory probes at 150, 200, 300, and 500 RPS caused multi-second
latencies and client-skipped requests; one create request failed at 500 RPS.
Because the load generator and Docker app shared this Windows machine, this
locates the local setup's stress limit, not a server-only capacity guarantee.
Use a separate load-generator host for a production capacity decision.

Redis remains deferred: mapping caches do not eliminate click-counter write
contention.

## Phase 4 — Scale architecture (4C implemented; remaining milestones gated)

The app is stateless across requests and persists URL mappings in PostgreSQL.
Milestone 4C is implemented as a durable PostgreSQL click-event queue and
separate worker, based on the redirect tail-latency measurement above. Milestone
4B (Redis) remains deferred because the observed bottleneck was a counter write.
Add multiple app instances behind a load balancer (4A) and database scaling
(4D) only when target capacity and follow-up measurements justify them. Re-run
the same benchmarks after each change.

## Phase 5 — Production readiness (implementation in progress)

Implemented locally: the API applies a per-process creation rate limit (default
60 requests per client per 60 seconds), emits security headers and HSTS when
configured with an HTTPS origin, and rejects credentialed redirect URLs,
localhost/private hostnames, and non-public literal IP addresses by default.
Configuration is documented in `.env.example`. The GitHub Actions workflow now
has read-only repository permissions. Sample Prometheus alert rules and an
operations runbook are in `ops/`.

The in-process limiter is a supplementary control, not a distributed limiter:
configure rate limits at the hosting edge as the authoritative abuse control.
Set `TRUST_PROXY_HEADERS=true` only when the trusted edge overwrites
`X-Forwarded-For` and blocks direct application access. The destination policy
does not perform DNS resolution and cannot detect a public-looking hostname
that resolves to a private address.

**Phase 5 is not complete.** This repository has not been deployed to a public
host; production secrets/TLS, an alert receiver, backup retention, and a
successful restore drill have not been configured or verified. Before launch,
follow [`ops/runbook.md`](ops/runbook.md), set service ownership and recovery
objectives, configure provider-level controls, and run and record a restore
drill. Do not mark the phase complete until the hosted smoke checks and restore
drill pass.

## Requirements and local setup

- Python 3.11+ and `uv`
- Docker Desktop with the Linux container engine, for the full PostgreSQL-backed
  application

Start the application and PostgreSQL:

```powershell
docker compose up --build -d
docker compose ps
```

The app is at `http://localhost:8000`; PostgreSQL is exposed on host port `5433`
to avoid conflicts with a PostgreSQL service already using port `5432`. The
Compose-only `postgres` / `postgres` credentials are for local development;
replace them with managed secrets in a hosted deployment. The named database
volume survives container restarts. `docker compose down -v` deletes it.

Run tests against SQLite or the running PostgreSQL container:

```powershell
uv sync
uv run pytest
$env:TEST_DATABASE_URL = 'postgresql+psycopg://postgres:postgres@localhost:5433/url_shortener'
uv run pytest
Remove-Item Env:TEST_DATABASE_URL
```

Use a dedicated empty database for `TEST_DATABASE_URL`; tests create persistent
sample links in that database.

### Configuration

Copy `.env.example` to `.env` for local host-based app runs. Set `DATABASE_URL`
to a reachable PostgreSQL database and `BASE_URL` to the public short-link
origin. The `DB_CONNECT_TIMEOUT_SECONDS`, `DB_STATEMENT_TIMEOUT_MS`,
`DB_POOL_TIMEOUT_SECONDS`, `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, and
`CODE_GENERATION_ATTEMPTS` settings have safe bounds. Link creation is limited
by `CREATE_RATE_LIMIT_COUNT` within `CREATE_RATE_LIMIT_WINDOW_SECONDS`.
`BLOCK_PRIVATE_DESTINATIONS` defaults to true; set it to false only for
controlled development environments that intentionally redirect to private
destinations. `TRUST_PROXY_HEADERS` defaults to false to prevent clients from
spoofing their limiter identity.

For Supabase, `DATABASE_URL` must be the PostgreSQL connection string from the
project's **Connect** dialog (typically starts with `postgresql://`), not the
Supabase **Project URL** (starts with `https://`) or an API key. In Render, set
the connection string as the `DATABASE_URL` secret environment variable and
prefer Supabase's session pooler when a direct connection is unavailable from
the hosting environment. If the password contains reserved URL characters,
URL-encode them before placing it in the connection string. Never post or
commit the connection string.

### API examples

Create a link:

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://localhost:8000/api/shorten `
  -ContentType 'application/json' `
  -Body '{"target_url":"https://example.com/docs","expires_at":"2030-01-01T00:00:00Z"}'
```

Open the returned `short_url` for a 302 redirect. Read
`GET /api/urls/{short_code}/stats` to see the eventually consistent click
count. Interactive API docs are available at `/docs`.

### SEO and Google indexing

The root page includes descriptive title and content for a URL shortener, short
link generator, click tracking, expiring links, and the URL shortener API,
along with canonical and Open Graph metadata, structured data, `robots.txt`,
and a sitemap. It is `noindex` by default so local/dev URLs are not presented
for indexing. For a deployed public site, set `BASE_URL` to its real HTTPS
origin and `SEO_INDEXABLE=true`; the sitemap is then available at
`/sitemap.xml`. Add the deployed site to Google Search Console and submit the
sitemap. These changes make the site crawlable and describe its content; they
cannot guarantee Google ranking. Google does not use the `meta keywords` tag
for ranking.

Render is a simple deployment option: deploy this repository as a Docker web
service and create a managed PostgreSQL database in the same region. Configure
the database connection string, `BASE_URL`, and `SEO_INDEXABLE` as service
environment variables, then use the generated public URL (or attach a custom
domain) as the canonical origin. Never use the local Compose credentials in
production.
