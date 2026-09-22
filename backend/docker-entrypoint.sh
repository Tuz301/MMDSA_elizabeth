#!/bin/sh
# Role dispatch for the application image.
#
# web    - migrate, then serve. Migrations run here, before gunicorn binds,
#          so an instance never serves requests against a schema it has not
#          reached. Django migrations take locks; concurrent runs from two
#          instances serialise on the database, and every migration in this
#          repository is additive, so the second run is a fast no-op.
# worker - the Celery worker: SMS dispatch, reconciliation, retention.
# beat   - the scheduler, behind a Redis lease so that every instance may
#          start one but only one actually schedules. See
#          scripts/beat_singleton.py.

set -eu

case "${CONTAINER_ROLE:-web}" in
  web)
    python manage.py migrate --noinput
    exec gunicorn config.wsgi:application \
      --bind 0.0.0.0:8000 \
      --workers "${GUNICORN_WORKERS:-3}" \
      --timeout 60 \
      --access-logfile - \
      --error-logfile -
    ;;
  worker)
    exec celery -A config worker --loglevel=INFO --concurrency "${CELERY_CONCURRENCY:-2}"
    ;;
  beat)
    exec python scripts/beat_singleton.py
    ;;
  *)
    echo "Unknown CONTAINER_ROLE '${CONTAINER_ROLE}'. Use web, worker or beat." >&2
    exit 64
    ;;
esac
