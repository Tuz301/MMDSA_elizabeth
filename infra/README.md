# Infrastructure

Four CDK stacks, deployed in order (CDK resolves the dependencies itself):
Network → Data → App → Observability.

```bash
npm ci
npx cdk synth --context env=pilot                      # read the diff first
npx cdk deploy --all --context env=pilot --context alarmEmail=ops@your-org.org
```

Region: af-south-1, an opt-in region. Enable it on the account before
bootstrapping, and read `lib/README-region-constraints.md` for what is and is
not verified there.

## Deployment

A release is a two-part fact: a container image in the `mmdsa-<env>` ECR
repository, and the tag the SSM parameter `/mmdsa/<env>/app-image-tag` points
at. Instances read the pointer at boot and run three containers from the one
image: web (migrate, then gunicorn on :8000), worker (Celery), and beat (the
scheduler, behind a Redis lease so only one schedules fleet-wide). The
freshly deployed parameter value is `bootstrap`, which means "no release
yet": instances come up, register with Session Manager, and serve nothing.

To ship a release:

```bash
./scripts/deploy-app.sh pilot v1.0.0
```

which does exactly four things — builds `backend/` for linux/arm64, pushes
the image, moves the pointer, and starts an ASG instance refresh so the
fleet re-boots onto the new tag one instance at a time. To roll back, run it
again with the previous tag: the image is still in the repository, and the
pointer is the only thing that moves.

The dashboard ships separately, because it is a static bundle:

```bash
./scripts/deploy-dashboard.sh pilot
```

which builds `dashboard/`, syncs `dist/` to the dashboard bucket, and
invalidates the CloudFront distribution. The bucket name and distribution
domain are stack outputs of `Mmdsa-<env>-App`.

## Before real patient data

The binding list lives in the repository README. In infrastructure terms:
set `certificateArn` (the plain-HTTP listener carries a synth warning until
you do), replace every `REPLACE_AFTER_DEPLOY` secret, set
`TERMII_WEBHOOK_SECRET`, supply a real `--context alarmEmail`, create an AWS
Budget with an alert (cost surprises are incidents too), and provision users
with `manage.py provision_user`, never by hand in two consoles.

## First deploy, in full

1. `aws account` — enable af-south-1, bootstrap CDK.
2. `npx cdk deploy --all --context env=pilot --context alarmEmail=...`
3. Replace the secrets in Secrets Manager; confirm the app settings module
   would accept them (`prod.py` refuses placeholders).
4. `./scripts/deploy-app.sh pilot v1.0.0`
5. One instance, via Session Manager:
   `docker exec mmdsa-web python manage.py seed_geography && docker exec mmdsa-web python manage.py seed_programme`
   (append `--sms-only` if the pilot launches without handsets).
6. `./scripts/deploy-dashboard.sh pilot`
7. Point the Termii dashboard's webhook at
   `https://<api>/api/v1/messaging/webhooks/termii/` with the same secret.
8. Provision the first system administrator with `provision_user`.
9. Run the go-live checks in `docs/RUNBOOK.md`.
