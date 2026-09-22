# Mentor Mother Digital Supervision Application

Backend and infrastructure for the PMTCT supervision pilot.
A joint project of IHVN and DataPharm Technologies Limited.

## What this system is for

An HIV exposed infant tests positive. The result reaches the facility. Weeks
pass before anybody acts, because in a paper system nothing prompts a person
until somebody reviews a register. By then the infant is not on treatment.

This system removes that delay. It does one thing: it makes the interval
between a result arriving and somebody acting on it visible, short and
measurable.

Everything else in the repository exists to serve that, or to keep the families
in the programme safe while it happens.

## Repository layout

```
infra/                    AWS CDK, TypeScript. Four stacks.
  bin/mmdsa.ts            Entrypoint. Wires the stacks together.
  config/environments.ts  Per-environment settings. Region af-south-1.
  lib/network-stack.ts    VPC, subnets, security groups, VPC endpoints.
  lib/data-stack.ts       PostgreSQL, Redis, KMS, S3, secrets.
  lib/app-stack.ts        Cognito, EC2 Auto Scaling group, ALB, WAF.
  lib/observability-stack.ts  Alarms and the dashboard.

backend/                  Django 5.2 LTS, Django REST Framework.
  config/                 Settings, URLs, Celery schedule.
  apps/common/            Base models, encrypted fields, metrics.
  apps/accounts/          Users, five-tier roles, row scoping.
  apps/registry/          Geography, facilities, mentor mothers, clients, infants.
  apps/visits/            Home visits, location verification, offline sync.
  apps/eid/               Test appointments, samples, results, ART linkage.
  apps/alerts/            The deterministic rule engine.
  apps/messaging/         Termii gateway, privacy guard, reply parsing.
  apps/audit/             The audit trail and the retention jobs.
  tests/                  The test suite.
```

## Running it

```bash
# Infrastructure. Synthesise without deploying.
cd infra
npm install
npx cdk synth --context env=pilot

# Backend. SQLite is used for checks and tests only.
cd backend
python -m venv .venv && ./.venv/bin/pip install -r requirements-dev.txt
USE_SQLITE=1 ./.venv/bin/python manage.py migrate
USE_SQLITE=1 ./.venv/bin/python manage.py seed_programme
USE_SQLITE=1 ./.venv/bin/python -m pytest tests/
```

Deploying to AWS needs the af-south-1 region enabled on the account. It is an
opt-in region.

## Four rules that shape the code

**The Baby Code rule.** An SMS crosses a public network and then sits in plain
text on a handset that other people in the household may use. A message that
names a child and implies an HIV exposure can get that family harmed. So every
outbound body identifies an infant by the Baby Code and by nothing else, and
that is enforced in `apps/messaging/guards.py` on every message after
rendering. It is not left to the care of whoever writes a template.

**Split responsibility.** A supervisor enters clinical data. A mentor mother
confirms an action. She is a peer supporter, not a clinician, and a shared write
path would leave the audit trail unable to say who recorded a result. Enforced
by `CanEnterClinicalData`, not only by the user interface.

**The location verdict is computed on the server.** A check that runs on a
handset can be defeated by that handset. The server holds the registered
household point and decides. There are five verdicts, not two, because the
target is 85 percent verified and honest failures exist: a wrong household
point, a weak fix under a roof, a handset with a poor receiver. Only
`OUT_OF_RANGE` raises a flag, and the alert wording asks the supervisor to check
the household point rather than to accuse anybody.

**Deny by default in the scoping layer.** A model not registered in
`SCOPE_PATHS` returns no rows. A developer who adds a model and forgets to
register it sees an empty list immediately. The opposite default would expose
every patient at every site and no test would fail.

## Decisions that need sign-off

**The pilot geography is Ogun and Plateau.** Decided September 2026. The
states and their local government areas load from
`apps/registry/fixtures/geography.json` via `manage.py seed_geography`, which
is idempotent and never deletes. No state name appears anywhere in the code,
the migrations or the constants — a test enforces this — so a change of pilot
site remains an edit to the fixture. LGA and facility pilot flags are set by
programme management once readiness is assessed.

**Django 5.2 LTS, not 4.2.** Annex B specifies 4.2 LTS, which left extended
support in April 2026. 5.2 is supported to 2028, past the scale-up phase. The
annex needs the correction.

**ALB with WAF, not API Gateway.** Annex B lists API Gateway. This build uses an
Application Load Balancer with an AWS WAF web ACL: same rate limiting and
request filtering, no per-request charge on top of the balancer, one fewer hop.
Recorded in the `AppStack` docstring rather than hidden.

## The dashboard

`dashboard/` holds the React supervisor dashboard (Vite + TypeScript). It
speaks only to the API above, renders programme codes whenever the server has
pruned an identifier for the caller's role, and reserves the colour red for
exactly two things: an unacknowledged positive result and a breached
deadline. See `dashboard/README.md` for how it runs.

## The API

The REST API lives under `/api/v1/`, with an authenticated OpenAPI viewer at
`/api/v1/schema/docs/`. Every list passes the scoping layer; identifier fields
are absent from responses for roles that may not read them; state changes are
POST actions, never PATCHes of status fields. The evaluation summary is at
`/api/v1/metrics/summary/`. The Termii callback endpoint is
`/api/v1/messaging/webhooks/termii/`: it verifies an HMAC-SHA512 signature
over the raw body and refuses every callback until the webhook secret is set.

## Deploying it

A release is an image in ECR plus the tag an SSM parameter points at;
`scripts/deploy-app.sh <env> <tag>` builds, pushes, moves the pointer and
refreshes the fleet, and rollback is the same script with the previous tag.
The dashboard ships with `scripts/deploy-dashboard.sh <env>` to a private
bucket behind CloudFront. Health workers are provisioned with
`manage.py provision_user`, which creates the Cognito account and the Django
row as one act. The full procedure is `infra/README.md`.

## The mentor mother channel

A mentor mother with a smartphone will use the React Native client, which is
not built and is a separately funded workstream. A mentor mother without one
works entirely by SMS: structured messages out, keyword replies in, and her
reply acknowledges the alert. A pilot that launches before handsets deploy
runs `seed_programme --sms-only`, which disables the two rules that watch
handset activity so that no supervisor learns to ignore alerts about devices
that do not exist. The undecided middle — half a smartphone rollout — is the
one configuration this repository refuses to make easy.

## What is not built

- The React Native client for mentor mothers (see the channel section above).
- A live Termii integration test. This is the largest remaining risk. The whole
  causal chain assumes two-way SMS is reliable on the pilot networks, and that
  assumption is currently untested against a real handset on a real carrier.

## Before real patient data enters any environment

- Set a TLS certificate ARN. Without one the listener is plain HTTP, and the
  CDK emits a warning saying so.
- Set `TERMII_WEBHOOK_SECRET`. The pilot settings refuse to start without it,
  and the webhook refuses every callback while it is unset.
- Replace every `REPLACE_AFTER_DEPLOY` value in Secrets Manager. The pilot
  settings module refuses to start while a placeholder remains.
- Supply a real alarm address (`--context alarmEmail=...`); the synth warns
  while the placeholder survives.
- Obtain the NHREC and state SHREC approvals described in Annex C. Consent
  is enforced in code — no client record exists before a consent timestamp
  and form reference, and no infant is registered under a mother without
  consent — but the approvals themselves are a programme act.
