# Operations runbook

How to run, deploy, and fix this system. Written for the engineer on call,
who may not be the engineer who built it.

## Run it locally

```bash
docker compose up -d                     # PostgreSQL 16 + Redis 7
cd backend
python -m venv .venv && ./.venv/bin/pip install -r requirements-dev.txt
DB_PASSWORD=mmdsa-dev DB_SSLMODE=disable ./.venv/bin/python manage.py migrate
DB_PASSWORD=mmdsa-dev DB_SSLMODE=disable ./.venv/bin/python manage.py seed_geography
DB_PASSWORD=mmdsa-dev DB_SSLMODE=disable ./.venv/bin/python manage.py seed_programme
DB_PASSWORD=mmdsa-dev DB_SSLMODE=disable ./.venv/bin/python manage.py runserver
# Tests use SQLite and need no services:
USE_SQLITE=1 ./.venv/bin/python -m pytest tests/

cd ../dashboard
npm install && npm run dev               # proxies /api to localhost:8000
```

## Deploy

Deployment is a deliberate operator act, not a push trigger.

```bash
cd infra
npm ci
npx cdk diff --context env=pilot         # read this. all of it.
npx cdk deploy --context env=pilot --all
```

Order on first deploy: Network, Data, App, Observability. CDK resolves the
dependencies itself. Before the first deploy with real data, the go-live
list in the README is binding: TLS certificate ARN, every secret replaced,
`TERMII_WEBHOOK_SECRET` set, ethics approvals on file. Also set an AWS
billing alarm — cost surprises are incidents too.

## Roll back

- **Application**: `git revert` the offending commit, push, redeploy the app
  stack. Under five minutes.
- **Infrastructure**: a failed stack update rolls back automatically; a bad
  successful one reverts by deploying the previous commit's synth.
- **A bad alert rule change**: no deploy needed — set `is_enabled=false` or
  restore the threshold in the admin; the audit log holds every prior value.

## When an alarm fires

**`EidResultsUnacknowledged48h` (the one that matters most).** A positive
result has sat unacknowledged past the SLA. This is a programme failure
before it is a technical one: check the alert row and its escalations in the
admin, confirm SMS delivery for the facility (below), then call the LGA
coordinator. The system's job was to make this visible; yours is to make
someone act.

**`SmsDeliveryFailed` rising.** Check `OutboundMessage` rows with status
FAILED/EXPIRED and their `last_error`. Termii-side errors (HTTP 4xx from the
gateway) mean the account or sender ID; carrier DND failures mean the
number. Failed messages are replayable: re-queue by resetting status to
QUEUED and calling `send_message.delay(id)` from a shell — dispatch
re-checks state, so a duplicate send cannot happen.

**`SyncRecordsOlderThan72h`.** A handset is holding visit records. Find the
device in `/api/v1/visits/sync-batches/`, identify the mentor mother through
her supervisor, and get the handset somewhere with signal. This is a
field-operations page, not a server page.

**Webhook signature failures in the logs.** One or two: noise on the open
internet. A stream: either the Termii dashboard secret and
`TERMII_WEBHOOK_SECRET` have drifted (re-sync them) or someone is probing;
the endpoint fails closed either way and the WAF holds the volume.

**Database unreachable.** The app's readiness endpoint goes 503 and the
pipeline stops promoting; the health endpoint stays 200 so the ALB does not
destroy instances that cannot fix the database. Check RDS events first; the
instances are fine.

## After any incident

Write the blameless note the same day: what was seen, when, what was done,
what will prevent it. File it in `docs/incidents/` by date. An incident that
produced no note will repeat.

## Where things are

| Thing | Place |
|---|---|
| API schema (authenticated) | `/api/v1/schema/docs/` |
| Evaluation numbers | `/api/v1/metrics/summary/` |
| Audit trail, soft-deleted rows, alert rules | Django admin |
| CloudWatch dashboard and alarms | Observability stack, `MMDSA/Programme` namespace |
| Programme thresholds | `settings.PROGRAMME` (deploy-time) and `AlertRule` rows (runtime) |
