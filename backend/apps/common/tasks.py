"""Scheduled jobs that compute and publish the programme metrics."""

from __future__ import annotations

from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone

from .metrics import put_metrics


@shared_task(name="apps.common.tasks.publish_programme_metrics")
def publish_programme_metrics() -> dict:
    """
    Compute the hourly programme metrics and publish them.

    These are the numbers the observability dashboard shows above the
    infrastructure row. A green infrastructure row with a failing SMS gateway
    means the intervention is not working, and only these metrics reveal that.
    """
    from apps.alerts.models import Alert
    from apps.eid.models import EidSample
    from apps.messaging.models import OutboundMessage
    from apps.visits.models import HomeVisit

    now = timezone.now()
    hour_ago = now - timedelta(hours=1)
    backlog_cut = now - timedelta(
        hours=settings.PROGRAMME["SYNC_BACKLOG_ALARM_HOURS"]
    )

    alerts = Alert.objects.aggregate(
        raised=Count("id", filter=Q(raised_at__gte=hour_ago)),
        acknowledged=Count("id", filter=Q(acknowledged_at__gte=hour_ago)),
        escalated=Count("id", filter=Q(escalated_at__gte=hour_ago)),
        past_sla=Count(
            "id",
            filter=Q(
                status__in=[Alert.Status.OPEN, Alert.Status.ESCALATED],
                acknowledge_by__lt=now,
            ),
        ),
    )

    sms = OutboundMessage.objects.filter(queued_at__gte=hour_ago).aggregate(
        dispatched=Count("id", filter=Q(status__in=["SENT", "DELIVERED", "FAILED"])),
        delivered=Count("id", filter=Q(status="DELIVERED")),
        failed=Count("id", filter=Q(status__in=["FAILED", "EXPIRED"])),
    )
    replies_parsed = OutboundMessage.objects.filter(
        reply_received_at__gte=hour_ago
    ).count()

    # Location verification is measured over 30 days, not one hour. An hourly
    # window at a small pilot site would swing on two or three visits and would
    # trigger an alarm that means nothing.
    window_start = timezone.localdate() - timedelta(days=30)
    visits = HomeVisit.objects.filter(visit_date__gte=window_start).aggregate(
        total=Count("id"),
        verified=Count("id", filter=Q(location_status="VERIFIED")),
    )
    verified_percent = (
        100.0 * visits["verified"] / visits["total"] if visits["total"] else 100.0
    )

    pending_sync = HomeVisit.objects.filter(server_received_at__isnull=True).count()
    stale_sync = HomeVisit.objects.filter(
        server_received_at__isnull=True, client_created_at__lt=backlog_cut
    ).count()

    unacknowledged = EidSample.objects.filter(
        result=EidSample.Result.POSITIVE,
        result_acknowledged_at__isnull=True,
        result_entered_at__lt=now - timedelta(hours=48),
    ).count()

    datapoints = [
        {"name": "AlertsRaised", "value": alerts["raised"], "unit": "Count"},
        {"name": "AlertsAcknowledged", "value": alerts["acknowledged"], "unit": "Count"},
        {"name": "AlertsEscalated", "value": alerts["escalated"], "unit": "Count"},
        {"name": "AlertsPastSla", "value": alerts["past_sla"], "unit": "Count"},
        {"name": "SmsDispatched", "value": sms["dispatched"], "unit": "Count"},
        {"name": "SmsDelivered", "value": sms["delivered"], "unit": "Count"},
        {"name": "SmsDeliveryFailed", "value": sms["failed"], "unit": "Count"},
        {"name": "SmsReplyParsed", "value": replies_parsed, "unit": "Count"},
        {
            "name": "VisitLocationVerifiedPercent",
            "value": round(verified_percent, 2),
            "unit": "Percent",
        },
        {"name": "SyncRecordsPending", "value": pending_sync, "unit": "Count"},
        {"name": "SyncRecordsOlderThan72h", "value": stale_sync, "unit": "Count"},
        {
            "name": "EidResultsUnacknowledged48h",
            "value": unacknowledged,
            "unit": "Count",
        },
    ]
    put_metrics(datapoints)
    return {point["name"]: point["value"] for point in datapoints}
