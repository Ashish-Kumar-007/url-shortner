# Operations runbook

This runbook is a starting point for operating the URL Shortener. Fill in the
deployment URL, service owner, on-call contact, and alert destination before
launch. Do not put credentials or production URLs containing credentials in
this file.

## Production configuration

- Use a managed PostgreSQL database and the hosting provider's secret store
  for `DATABASE_URL`; never use the Compose `postgres` / `postgres` credentials.
- Set `BASE_URL` to the canonical public HTTPS origin. HSTS is sent when this
  setting uses `https://`; TLS must terminate at the hosting platform or trusted
  reverse proxy.
- Keep `SEO_INDEXABLE=false` until the public site is ready to be indexed.
- Keep `BLOCK_PRIVATE_DESTINATIONS=true`. It rejects credentialed URLs, local
  hostnames, and non-public literal IP addresses. It does not resolve DNS, so it
  cannot detect a public-looking hostname that resolves to a private address.
- Set `TRUST_PROXY_HEADERS=true` only when the edge proxy overwrites
  `X-Forwarded-For` and direct access to the application is blocked. Otherwise,
  clients could spoof their rate-limit identity.
- The application create limiter is in-process and per replica. Configure
  provider/edge rate limits as the authoritative distributed control. The
  application default is 60 link-creation requests per client per 60 seconds.
- Keep `/metrics/` private to the monitoring network where the hosting platform
  permits path-level access controls. The endpoint has no authentication.
- Store backups encrypted and outside the database provider's failure domain.
  Set retention and recovery objectives with the service owner before launch.

## Monitoring and alerts

`prometheus-alerts.yml` contains sample Prometheus alerts for target
availability, 5xx rate, and p95 latency. Configure Prometheus to scrape
`/metrics/` with the job label `url-shortener`, load the rules, and connect an
Alertmanager receiver (email, pager, or incident system). These files do not
provision a monitoring service or send notifications by themselves.

Inspect structured app and worker logs for `request_id`, exceptions, database
failures, and click-event processing. Confirm the hosting platform retains logs
long enough for incident investigation. The worker currently has no dedicated
Prometheus health metric; monitor its process and logs until that metric is
added.

## Backup and restore drill

Install PostgreSQL client tools (`pg_dump`, `pg_restore`, and `psql`) on a
trusted operator machine. Set `DATABASE_URL` in the operator's secret-managed
environment; do not paste it into a command or commit it. Create a custom-format
backup and retain it in encrypted offsite storage:

```powershell
New-Item -ItemType Directory -Force backups | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = "backups/url-shortener-$stamp.dump"
pg_dump --format=custom --no-owner --no-acl --file $backup $env:DATABASE_URL
pg_restore --list $backup
```

For a restore drill, create a **new disposable PostgreSQL database** and set
`RESTORE_TEST_DATABASE_URL` to its connection string. Never point this command
at production or the source database: `--clean` drops objects in the target.

```powershell
pg_restore --clean --if-exists --no-owner --no-acl `
  --dbname $env:RESTORE_TEST_DATABASE_URL $backup
psql $env:RESTORE_TEST_DATABASE_URL `
  -c 'SELECT count(*) AS urls FROM urls;'
psql $env:RESTORE_TEST_DATABASE_URL `
  -c 'SELECT count(*) AS pending_click_events FROM click_events;'
```

Record the backup timestamp, restore duration, row counts, and any errors in
the team's incident/change log. Verify the restored service against the smoke
checks below before considering the drill successful.

## Incident response and rollback

1. Check the hosting provider's service status, `/health/live`,
   `/health/ready`, application logs, worker logs, and PostgreSQL availability.
2. For elevated 5xx or latency, identify the affected route and database errors
   from Prometheus/logs. Roll back to the last known-good image/release using
   the hosting provider's deployment controls; do not run destructive Git
   commands or roll back database data without a verified backup.
3. If click statistics lag, verify the worker is running and inspect its
   database errors and pending click-event count. Redirects enqueue durable
   events, so do not manually delete unprocessed events.
4. If the database is unavailable, preserve the current database and logs,
   restore only into a separate database first, and obtain owner approval before
   any production data replacement.
5. After recovery, check `/health/ready`, create a test link, follow its
   redirect, and confirm its click count advances after the worker runs. Record
   the incident, customer impact, and follow-up actions.

## Release smoke checks

- `/health/live` returns 200 and `/health/ready` returns 200.
- Create a link, verify its destination and expiration, follow the 302, and
  confirm stats eventually reflect the click.
- A burst over the configured create rate returns 429 with `Retry-After`.
- HTTP requests receive HTTPS at the public edge; the HTTPS response includes
  HSTS and the security headers.
- Check `/metrics/` from the monitoring network and verify alert delivery with
  a planned test before relying on alerts.
