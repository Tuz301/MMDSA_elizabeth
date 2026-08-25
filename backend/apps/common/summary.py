"""
The pilot evaluation summary.

Every indicator here is deterministic and derivable from existing fields, so
the evaluation cannot be told a number the data cannot back. Medians are
computed in Python over the scoped rows: the pilot cohort is bounded, and a
database-specific percentile function would tie the endpoint to Postgres
while the checks run on SQLite.

Scoping applies to every queryset, so a facility supervisor's summary covers
their facility, an LGA coordinator's their area, and so on. No patient
identifier appears in the response: counts, medians and percentages only.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db.models import Count
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from apps.accounts.permissions import IsActiveHealthWorker
from apps.accounts.scoping import scope_queryset
from apps.alerts.models import Alert
from apps.eid.models import ArtLinkage, EidSample
from apps.messaging.models import OutboundMessage
from apps.visits.models import HomeVisit


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[mid], 1)
    return round((ordered[mid - 1] + ordered[mid]) / 2, 1)


@api_view(["GET"])
@permission_classes([IsActiveHealthWorker])
def metrics_summary(request):
    """
    GET /api/v1/metrics/summary/?date_from=&date_to=

    The window defaults to the last 90 days. unacknowledged_positives is a
    now-number and ignores the window: an old unacknowledged positive is more
    urgent, not less.
    """
    today = timezone.localdate()
    date_from = request.query_params.get("date_from") or str(today - timedelta(days=90))
    date_to = request.query_params.get("date_to") or str(today)
    user = request.user

    samples = scope_queryset(EidSample.objects.all(), user)
    linkages = scope_queryset(ArtLinkage.objects.all(), user)
    alerts = scope_queryset(Alert.objects.all(), user)
    visits = scope_queryset(HomeVisit.objects.all(), user)
    messages = scope_queryset(OutboundMessage.objects.all(), user)

    # -- The relay delay: the interval the intervention targets -------------
    unacknowledged_positives = samples.filter(
        result=EidSample.Result.POSITIVE, result_acknowledged_at__isnull=True
    ).count()

    acknowledged = samples.filter(
        result_entered_at__date__range=(date_from, date_to),
        result_acknowledged_at__isnull=False,
    ).values_list("result_entered_at", "result_acknowledged_at", "result")
    relay_all = [
        (ack - entered).total_seconds() / 3600 for entered, ack, _ in acknowledged
    ]
    relay_positive = [
        (ack - entered).total_seconds() / 3600
        for entered, ack, result in acknowledged
        if result == EidSample.Result.POSITIVE
    ]

    # -- The primary outcome: days from result issue to treatment -----------
    linkage_target_days = settings.PROGRAMME.get("LINKAGE_TARGET_DAYS", 14)
    started = linkages.filter(
        status=ArtLinkage.Status.STARTED,
        art_start_date__isnull=False,
        art_start_date__range=(date_from, date_to),
        triggering_sample__result_issued_on__isnull=False,
    ).values_list("art_start_date", "triggering_sample__result_issued_on")
    days_to_linkage = [(start - issued).days for start, issued in started]

    # The cohort for the linkage-within-target share: every positive result
    # issued in the window that has had the full target period to link,
    # including refusals and pending rows. Excluding them would flatter the
    # intervention.
    cohort_cutoff = today - timedelta(days=linkage_target_days)
    cohort = linkages.filter(
        triggering_sample__result_issued_on__isnull=False,
        triggering_sample__result_issued_on__range=(date_from, date_to),
        triggering_sample__result_issued_on__lte=cohort_cutoff,
    )
    cohort_total = cohort.count()
    cohort_linked = sum(
        1
        for start, issued in cohort.filter(
            status=ArtLinkage.Status.STARTED, art_start_date__isnull=False
        ).values_list("art_start_date", "triggering_sample__result_issued_on")
        if (start - issued).days <= linkage_target_days
    )

    # Data completeness: a linkage whose sample has no lab issue date drops
    # out of the indicator. Reporting the count keeps the denominator honest.
    missing_issue_date = linkages.filter(
        triggering_sample__result_issued_on__isnull=True
    ).count()

    # -- Alert SLA ----------------------------------------------------------
    window_alerts = alerts.filter(
        raised_at__date__range=(date_from, date_to)
    ).exclude(status=Alert.Status.CANCELLED)
    alerts_total = window_alerts.count()
    alerts_in_sla = sum(
        1
        for acknowledged_at, deadline in window_alerts.filter(
            acknowledged_at__isnull=False
        ).values_list("acknowledged_at", "acknowledge_by")
        if acknowledged_at <= deadline
    )
    alerts_past_deadline_now = alerts.filter(
        status__in=[Alert.Status.OPEN, Alert.Status.ESCALATED],
        acknowledge_by__lt=timezone.now(),
    ).count()

    # -- Location verification ----------------------------------------------
    window_visits = visits.filter(visit_date__range=(date_from, date_to))
    visits_total = window_visits.count()
    verdicts = {
        row["location_status"]: row["n"]
        for row in window_visits.values("location_status").annotate(n=Count("id"))
    }
    verified = verdicts.get(HomeVisit.LocationStatus.VERIFIED, 0)

    # -- SMS delivery --------------------------------------------------------
    attempted = messages.filter(
        queued_at__date__range=(date_from, date_to),
        status__in=[
            OutboundMessage.Status.SENT,
            OutboundMessage.Status.DELIVERED,
            OutboundMessage.Status.FAILED,
            OutboundMessage.Status.EXPIRED,
        ],
    )
    attempted_total = attempted.count()
    failed = attempted.filter(
        status__in=[OutboundMessage.Status.FAILED, OutboundMessage.Status.EXPIRED]
    ).count()
    blocked = messages.filter(
        queued_at__date__range=(date_from, date_to),
        status=OutboundMessage.Status.BLOCKED,
    ).count()

    def pct(numerator: int, denominator: int) -> float | None:
        return round(100 * numerator / denominator, 1) if denominator else None

    return Response(
        {
            "window": {"date_from": date_from, "date_to": date_to},
            "eid_cascade": {
                "unacknowledged_positives_now": unacknowledged_positives,
                "relay_delay_median_hours_all": _median(relay_all),
                "relay_delay_median_hours_positive": _median(relay_positive),
            },
            "art_linkage": {
                "days_to_linkage_median": _median([float(d) for d in days_to_linkage]),
                "linkage_target_days": linkage_target_days,
                "pct_linked_within_target": pct(cohort_linked, cohort_total),
                "cohort_size": cohort_total,
                "linkages_missing_issue_date": missing_issue_date,
            },
            "alerts": {
                "raised": alerts_total,
                "sla_compliance_pct": pct(alerts_in_sla, alerts_total),
                "past_deadline_now": alerts_past_deadline_now,
            },
            "location_verification": {
                "visits": visits_total,
                "verified_pct": pct(verified, visits_total),
                "target_pct": settings.PROGRAMME["LOCATION_VERIFIED_TARGET_PERCENT"],
                # The five-verdict breakdown, because a single percentage
                # invites reading an unverified visit as misconduct.
                "verdicts": verdicts,
            },
            "sms": {
                "dispatch_attempted": attempted_total,
                "delivery_failure_pct": pct(failed, attempted_total),
                "blocked_by_privacy_guard": blocked,
            },
        }
    )
