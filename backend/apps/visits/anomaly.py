"""
Server-side geospatial anomaly detection.

The job looks for patterns that no single visit record can show. It runs
nightly, over the previous day.

Every threshold is conservative. A false accusation against a mentor mother
damages the trust the whole peer support model rests on, and that trust is
harder to rebuild than a dashboard is to fix. The job therefore favours missing
a real anomaly over raising a false one.

Nothing here produces a score, a ranking or a judgement. It produces a prompt
for a supervisor to have a conversation.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from .models import GeospatialAnomaly, HomeVisit, haversine_metres

#: A generous upper bound on travel speed in a rural Nigerian setting. Anything
#: above this is physically implausible rather than merely fast.
MAX_PLAUSIBLE_SPEED_KMH = 90.0

#: Two readings closer than this are treated as the same position. A consumer
#: satellite receiver does not repeat a fix to the metre.
IDENTICAL_POSITION_METRES = 5.0

#: The number of identical positions in one day before an anomaly is raised.
IDENTICAL_POSITION_COUNT = 4


def run_detection(for_date=None) -> dict:
    """Run every detector over one day of visits."""
    target = for_date or (timezone.localdate() - timedelta(days=1))
    raised = {
        "impossible_travel": _detect_impossible_travel(target),
        "identical_position": _detect_identical_positions(target),
    }
    return {"date": str(target), **raised}


def _detect_impossible_travel(target) -> int:
    """Find consecutive visits that could not have been made in the time."""
    raised = 0
    visits = (
        HomeVisit.objects.filter(
            visit_date=target, latitude__isnull=False, longitude__isnull=False
        )
        .exclude(location_captured_at__isnull=True)
        .select_related("mentor_mother")
        .order_by("mentor_mother_id", "location_captured_at")
    )

    by_worker: dict = {}
    for visit in visits:
        by_worker.setdefault(visit.mentor_mother_id, []).append(visit)

    for worker_visits in by_worker.values():
        for earlier, later in zip(worker_visits, worker_visits[1:]):
            gap_hours = (
                later.location_captured_at - earlier.location_captured_at
            ).total_seconds() / 3600
            if gap_hours <= 0:
                continue

            distance_km = (
                haversine_metres(
                    float(earlier.latitude),
                    float(earlier.longitude),
                    float(later.latitude),
                    float(later.longitude),
                )
                / 1000
            )
            speed = distance_km / gap_hours
            if speed <= MAX_PLAUSIBLE_SPEED_KMH:
                continue

            anomaly, created = GeospatialAnomaly.objects.get_or_create(
                mentor_mother=earlier.mentor_mother,
                kind=GeospatialAnomaly.Kind.IMPOSSIBLE_TRAVEL,
                detected_for_date=target,
                defaults={
                    "detail": (
                        f"Two visits {distance_km:.1f} km apart were recorded "
                        f"{gap_hours:.1f} hours apart, which implies "
                        f"{speed:.0f} km/h. Check whether one of the household "
                        f"positions is registered wrongly."
                    ),
                    "confidence": "MEDIUM",
                },
            )
            if created:
                anomaly.visits.set([earlier, later])
                raised += 1
    return raised


def _detect_identical_positions(target) -> int:
    """Find several visits reporting the same position on one day."""
    raised = 0
    visits = (
        HomeVisit.objects.filter(
            visit_date=target, latitude__isnull=False, longitude__isnull=False
        )
        .select_related("mentor_mother")
        .order_by("mentor_mother_id")
    )

    by_worker: dict = {}
    for visit in visits:
        by_worker.setdefault(visit.mentor_mother_id, []).append(visit)

    for worker_visits in by_worker.values():
        if len(worker_visits) < IDENTICAL_POSITION_COUNT:
            continue

        clusters: list[list] = []
        for visit in worker_visits:
            placed = False
            for cluster in clusters:
                head = cluster[0]
                distance = haversine_metres(
                    float(head.latitude),
                    float(head.longitude),
                    float(visit.latitude),
                    float(visit.longitude),
                )
                if distance <= IDENTICAL_POSITION_METRES:
                    cluster.append(visit)
                    placed = True
                    break
            if not placed:
                clusters.append([visit])

        for cluster in clusters:
            if len(cluster) < IDENTICAL_POSITION_COUNT:
                continue
            # Several visits to one compound is normal and common. Only raise
            # the anomaly where the visits are to different clients.
            distinct_clients = {visit.client_id for visit in cluster}
            if len(distinct_clients) < IDENTICAL_POSITION_COUNT:
                continue

            anomaly, created = GeospatialAnomaly.objects.get_or_create(
                mentor_mother=cluster[0].mentor_mother,
                kind=GeospatialAnomaly.Kind.IDENTICAL_POSITION,
                detected_for_date=target,
                defaults={
                    "detail": (
                        f"{len(cluster)} visits to {len(distinct_clients)} "
                        f"different clients report the same position to within "
                        f"{IDENTICAL_POSITION_METRES:.0f} m. This can happen "
                        f"where a handset caches a fix. Confirm with the "
                        f"mentor mother before drawing any conclusion."
                    ),
                    "confidence": "LOW",
                },
            )
            if created:
                anomaly.visits.set(cluster)
                raised += 1
    return raised
