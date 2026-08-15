"""Celery application and the scheduled task calendar."""

from __future__ import annotations

import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("mmdsa")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# The times below are West Africa Time. Evaluation jobs run early, before the
# working day, so that a supervisor arrives to a dashboard that is already
# current.
app.conf.beat_schedule = {
    "raise-alerts": {
        "task": "apps.alerts.tasks.run_alert_rules",
        "schedule": crontab(minute="*/15"),
    },
    "escalate-overdue-alerts": {
        "task": "apps.alerts.tasks.escalate_overdue_alerts",
        "schedule": crontab(minute=5, hour="*"),
    },
    "send-eid-reminders": {
        "task": "apps.alerts.tasks.send_appointment_reminders",
        "schedule": crontab(minute=0, hour=7),
    },
    "detect-geospatial-anomalies": {
        "task": "apps.visits.tasks.detect_anomalies",
        "schedule": crontab(minute=30, hour=2),
    },
    "publish-programme-metrics": {
        "task": "apps.common.tasks.publish_programme_metrics",
        "schedule": crontab(minute=0),
    },
    "archive-audit-log": {
        "task": "apps.audit.tasks.archive_to_s3",
        "schedule": crontab(minute=0, hour=1),
    },
    "apply-retention-policy": {
        "task": "apps.audit.tasks.apply_retention_policy",
        "schedule": crontab(minute=0, hour=3, day_of_month=1),
    },
    "reconcile-sms-delivery": {
        "task": "apps.messaging.tasks.reconcile_delivery_reports",
        "schedule": crontab(minute="*/30"),
    },
}
