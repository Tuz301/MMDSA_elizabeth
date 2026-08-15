"""Nightly geospatial anomaly detection."""

from celery import shared_task


@shared_task(name="apps.visits.tasks.detect_anomalies")
def detect_anomalies() -> dict:
    from .anomaly import run_detection

    return run_detection()
