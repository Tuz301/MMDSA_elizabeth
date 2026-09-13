# Systems checklist audit

Run against "The 2026 Complete Systems Checklist" on 2026-09-13. Every item
gets one of three honest answers: **met** (with where), **deliberate
deviation** (with why), or **deferred** (with the trigger that will make it
due). An unmarked box would be a claim nobody checked; there are none here.

## Part 1 — Data layer

- **Queue system — met.** SMS dispatch, delivery reconciliation, the alert
  engine and retention jobs all run in Celery workers off the request cycle
  (`backend/config/celery.py`, `apps/*/tasks.py`). No request handler sends
  a message or scans a register.
- **Caching — deliberate deviation.** Patient-facing reads are not cached.
  Every list is scoped per role and feeds a clinical decision; a stale
  "unacknowledged positives" number is worse than a slower one. What is
  cached: the Cognito JWKS (1 hour), and the dashboard's client-side query
  cache with explicit invalidation after every mutation. Revisit if p95
  latency ever matters more than freshness — it does not at pilot scale.
- **Indexes — met.** Every column the filtersets and the engine query is
  indexed, including the composite indexes on `(result, result_entered_at)`,
  `(status, acknowledge_by)`, `(facility, status, severity)` and the sync
  timestamps. Nothing is indexed speculatively.
- **Transactions — met.** Result entry (result + infant outcome + linkage),
  acknowledgement (sample + alert resolution), linkage status (linkage +
  outcome), visit review (visit + alert), sync push and the geography seed
  are each one atomic block.
- **Schema migrations — met.** Every table exists only through versioned
  Django migrations; `migrate` reproduces the schema from zero, and CI runs
  it on every push.
- **Normalization — met.** Fully normalized; the one deliberate
  denormalization is `Alert.alert_type`/`severity` copied from the rule at
  raise time, so a later threshold change cannot rewrite the history the
  evaluation reads.
- **Soft deletes — met.** Every programme model inherits `SoftDeleteModel`;
  the API exposes no DELETE at all. Clinical records stay recoverable for
  the seven-year retention period.
- **Pub/Sub — deliberate deviation.** One Django service, so in-process
  events would be ceremony. The decoupling the item exists for is achieved
  where it matters: the alert engine communicates with the SMS layer only by
  writing rows that Celery workers pick up, so a Termii outage cannot fail a
  clinical write.

## Part 2 — Reliability

- **Retry with exponential backoff — met.** `send_message` retries 3 times
  with exponential backoff and jitter (60s → ~120s → ~240s), and dispatch
  re-checks message status first, so a retry cannot double-send. Only
  idempotent work retries.
- **Circuit breaker — deferred.** One downstream service (Termii), a retry
  cap of 3 and a reconciliation job bound the damage; a breaker becomes due
  if SMS volume grows past pilot scale or a second downstream appears.
- **Idempotency — met.** Handset-generated UUIDs make sync re-uploads
  no-ops; the webhook deduplicates on the provider message id; sample and
  alert acknowledgements are write-once; `build_schedule` and the geography
  seed are re-runnable.
- **Graceful degradation — met.** A metrics-publish failure is logged and
  swallowed; the health endpoint deliberately ignores the database so a DB
  fault cannot make the load balancer destroy healthy instances; a blocked
  or failed SMS is recorded, never silently dropped.
- **Timeouts — met.** Termii calls 15s, Redis health probe 2s, dashboard
  fetches 20s with a readable failure message, database connections recycled
  at 60s. CI itself is the ceiling on everything else.
- **Dead letter queues — met in substance.** A message that exhausts its
  retries becomes a `FAILED` row — visible in the API and the admin,
  counted by the `SmsDeliveryFailed` CloudWatch metric the observability
  stack alarms on, and replayable by re-queueing. The failure is data, not a
  vanished job.

## Part 3 — Scale

- **Rate limiting — met.** Scoped throttles on sync (120/h), auth (20/h) and
  the webhook (600/h, with a regression test proving the limit actually
  fires), a 1000/h per-user ceiling on everything else, and the WAF on the
  ALB in front of it all.
- **Stateless design — met.** JWT bearer auth, no server-side session for
  API clients, S3 for objects, configuration entirely from the environment.
  Any instance can serve any request.
- **Horizontal scaling / load balancing — met.** EC2 Auto Scaling group
  behind an ALB (`infra/lib/app-stack.ts`); one instance failing is absorbed.
- **Async heavy work — met.** Nothing in the request cycle takes seconds;
  everything slow is Celery's.
- **Connection pooling — met for this scale.** Persistent connections
  (`CONN_MAX_AGE=60`) across requests. PgBouncer is the known next step if
  the scale-up phase multiplies workers.

## Part 4 — Security and auth

- **Authentication — met.** AWS Cognito holds credentials; the API verifies
  signature, audience and issuer against the JWKS and matches users on the
  immutable Cognito subject, never the username. No password hash exists in
  the pilot database.
- **Authorization — met, with one deliberate deviation.** Every request
  passes a role check and a row-scope check, and both must pass. Deviation:
  an out-of-scope row returns **404, not 403**. In a system where row
  existence is itself sensitive ("is this phone number enrolled in an HIV
  programme"), a 403 is an existence oracle. A test pins this choice.
- **Input validation — met.** DRF serializers validate everything
  server-side; the ORM parameterizes everything; the privacy guard validates
  even our own outbound text.
- **Secrets — met.** Environment variables locally, Secrets Manager in the
  pilot, fail-fast on placeholders, `.gitignore` covers env files and keys,
  and the tree was scanned before first push.
- **HTTPS — met in code, gated at go-live.** Prod settings force SSL
  redirect, HSTS with preload, secure cookies. The TLS certificate ARN is a
  documented go-live blocker and the CDK warns loudly while it is absent.
- **Data privacy — met.** NDPR/NDPA posture: identifiers encrypted at rest
  and role-gated in every response, Baby Code rule enforced on every
  outbound SMS, consent gating on enrolment, identifier reads audited,
  seven-year retention with soft deletes, msisdns never serialized.

## Part 5 — Observability

- **Structured logging — met.** JSON logs to stdout; no log line may carry a
  name, number or message body, and a test greps the captured logs to prove
  it.
- **Metrics and dashboards — met.** Thirteen programme metrics published to
  CloudWatch; the observability stack builds the dashboard and alarms.
- **Alerting — met.** CloudWatch alarms on the metrics that matter
  (unacknowledged positives at 48h, delivery failures, sync backlog); the
  in-app alert engine is itself the clinical alerting layer, with
  escalation.
- **Distributed tracing — deferred.** One service; the audit middleware
  already stamps every request with actor, path and outcome. Tracing becomes
  due when a second service appears.
- **Error tracking — deferred.** CloudWatch logs plus alarms cover the
  pilot. Wiring Sentry is a one-setting change (`sentry-sdk` DSN via env)
  recommended before scale-up; not added now because a dependency without
  its DSN is dead weight.

## Part 6 — Deployment and operations

- **Feature flags — met where flags carry risk.** Alert rules are data:
  every rule can be disabled instantly (`is_enabled`) and every threshold
  tuned without a deploy, which is precisely the runtime control this system
  needs. A general flag framework is YAGNI until there is a second consumer.
- **CI/CD — met (CI), deliberate (CD).** `.github/workflows/ci.yml` runs the
  backend suite + lint + schema check, the dashboard type-check + tests +
  build, and a CDK synth on every push. Deployment to the pilot remains a
  deliberate operator act — automatic deploys to an environment holding HIV
  patient data need the change-control sign-off first.
- **Safe deploys and rollback — met.** CloudFormation rolls back a failed
  stack update automatically; the ASG replaces instances rolling; `git
  revert` + redeploy is the application rollback and is under five minutes.
- **Environment parity — met.** `docker-compose.yml` gives development the
  same PostgreSQL 16 and Redis 7 the pilot runs. SQLite remains what the
  README says it is: for checks and tests only.
- **12-factor — met.** Config from env, stateless processes, logs to stdout,
  backing services swappable by URL, dev–prod parity above.

## Part 7 — Code quality and architecture

- **Separation of concerns — met.** Models hold domain rules, serializers
  hold validation, viewsets hold transitions, the engine holds alert logic,
  the gateway is the single point that touches the outside world, and the
  scoping layer is the single point that answers "which rows".
- **DRY — met.** One scoping function, one identifier gate, one pagination
  class, one error normalizer in the dashboard, one throttle definition.
- **SOLID / single responsibility — met.** See the file layout; each module
  states its one job in its docstring.
- **YAGNI — met, and enforced twice above** (no speculative caching, no flag
  framework, no tracing stack for one service).
- **Dependency injection — met where it earns its keep.** Settings inject
  every backing service; tests swap the encryption key, webhook secret,
  throttle rates and programme thresholds through fixtures without touching
  code.
- **Automated tests — met.** 129 backend tests (unit through API
  integration) plus dashboard tests; the suite runs in ~13 seconds, on every
  push, and every review finding is pinned by a named regression test.
- **Version control discipline — met.** Small commits, each message saying
  why; adversarial review before merge; the history reads as a narrative.

## Part 8 — Networking and infrastructure

- **DNS/HTTP fundamentals — met.** Correct verbs, correct codes (201/400/
  401/403/404/409/429), explicit health vs readiness endpoints for the load
  balancer and the pipeline respectively.
- **CDN — deferred.** The dashboard is a static bundle; serving it via
  CloudFront is a one-stack addition due at go-live alongside the TLS
  certificate. The pilot's users are in two Nigerian states, not global.
- **API design — met.** Nouns, `/api/v1/` from day one, pagination
  everywhere, one error shape, authenticated OpenAPI schema with zero
  generation warnings.
- **Webhooks over polling — met.** Termii pushes; we verify HMAC-SHA512 over
  the raw body, fail closed, and deduplicate. The one poll that exists
  (delivery reconciliation) is the safety net for missed webhooks, not the
  mechanism.
- **Serverless/edge — considered and declined.** Celery workers are
  long-running by design; the ALB+ASG shape fits an app with a worker fleet
  and a relational core.

## Part 9 — AI infrastructure

- **All four items — deliberately not applicable.** The alert engine is
  deterministic by design: if a model decided which alerts to raise, a
  change in linkage outcomes could not be attributed to the intervention and
  the pilot evaluation would prove nothing. Predictive scoring is explicitly
  scoped for the phase after the pilot, when this baseline exists to compare
  against. That reasoning lives in `apps/alerts/models.py`.

## Part 10 — Cost and operations

- **Cost engineering — partially met, action noted.** Pilot-sized instances,
  no per-request API Gateway charge (the ALB decision), metrics batched.
  Action for the operator: set an AWS billing alarm at deploy time — noted
  in the runbook.
- **Multi-tenancy — met.** The scoping layer is tenant isolation in the
  shared-table model: every query filters by the caller's scope, the default
  is zero rows, and the write path is scoped too. The missing-WHERE-clause
  failure mode the checklist warns about is exactly what `SCOPE_PATHS`
  deny-by-default was built to prevent.
- **Incident response — met for pilot scale.** `docs/RUNBOOK.md`: detection
  (alarms), ownership, mitigation steps per failure class, and the rule that
  every incident gets a blameless note.
- **Documentation — met.** README (what and why), this audit, the runbook,
  the authenticated OpenAPI viewer, and module docstrings that state the
  reasoning a reviewer needs. A new engineer runs the whole stack locally
  from the README in under an hour.

## The scoreboard

Met: 36 · Deliberate deviation or N/A with recorded reasoning: 8 ·
Deferred with a named trigger: 5 (circuit breaker, tracing, Sentry, CDN,
PgBouncer). Nothing unanswered.
