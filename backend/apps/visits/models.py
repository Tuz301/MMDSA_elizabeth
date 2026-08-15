"""
Home visit records, location verification and geospatial anomaly detection.

The location check runs on the server, never on the handset. A check that runs
on a handset can be defeated by an application that reports a false position,
and the handset has no way to prove that it did not. The server holds the
registered household point, computes the distance, and records a verdict that
the handset cannot change.

The target is that at least 85 percent of home visits carry a verified location
record. The target is not 100 percent, and the design reflects that. A reading
can fail for honest reasons: a household point recorded wrongly at enrolment,
poor satellite reception under a roof, a handset with a weak receiver. An
unverified visit is flagged for review. It is never treated as proof of
misconduct.
"""

from __future__ import annotations

import math
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel, SyncableModel


def haversine_metres(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """Return the great circle distance between two points, in metres."""
    radius = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(a))


class HomeVisit(SyncableModel):
    """
    A visit by a mentor mother to a client's household.

    The record is created on the handset, often with no network. It carries the
    identifier and the timestamp from the handset, so the record keeps its
    meaning when it synchronises days later.
    """

    class Purpose(models.TextChoices):
        ROUTINE = "ROUTINE", "Routine support visit"
        MISSED_APPOINTMENT = "MISSED_APPT", "Follow up on a missed appointment"
        EID_REMINDER = "EID_REMINDER", "Reminder of an infant test appointment"
        ART_LINKAGE = "ART_LINKAGE", "Support linkage of an infant to treatment"
        TRACING = "TRACING", "Trace a client who has stopped attending"
        POSTNATAL = "POSTNATAL", "Postnatal support"

    class Result(models.TextChoices):
        COMPLETED = "COMPLETED", "Client met, visit completed"
        CLIENT_ABSENT = "ABSENT", "Household reached, client absent"
        HOUSEHOLD_MOVED = "MOVED", "Household has moved away"
        REFUSED = "REFUSED", "Client declined the visit"
        NOT_REACHED = "NOT_REACHED", "Household could not be reached"

    class LocationStatus(models.TextChoices):
        VERIFIED = "VERIFIED", "Position matches the registered household"
        OUT_OF_RANGE = "OUT_OF_RANGE", "Position is outside the match radius"
        LOW_ACCURACY = "LOW_ACCURACY", "Reading is too imprecise to judge"
        NO_FIX = "NO_FIX", "The handset obtained no position"
        NO_REFERENCE = "NO_REFERENCE", "No household point is registered"

    client = models.ForeignKey(
        "registry.Client", on_delete=models.PROTECT, related_name="home_visits"
    )
    mentor_mother = models.ForeignKey(
        "registry.MentorMother", on_delete=models.PROTECT, related_name="home_visits"
    )
    infant = models.ForeignKey(
        "registry.Infant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="home_visits",
    )

    purpose = models.CharField(max_length=16, choices=Purpose.choices)
    result = models.CharField(max_length=12, choices=Result.choices)
    visit_date = models.DateField(db_index=True)
    duration_minutes = models.PositiveSmallIntegerField(null=True, blank=True)
    notes = models.TextField(
        blank=True,
        max_length=1000,
        help_text=(
            "Support notes only. Do not record a clinical finding here. "
            "A clinical entry belongs to a supervisor."
        ),
    )

    # -- Position reported by the handset ---------------------------------
    latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    location_accuracy_metres = models.FloatField(
        null=True,
        blank=True,
        help_text="Accuracy radius the handset reported for the reading.",
    )
    location_captured_at = models.DateTimeField(null=True, blank=True)

    # -- Verdict computed by the server -----------------------------------
    location_status = models.CharField(
        max_length=14,
        choices=LocationStatus.choices,
        default=LocationStatus.NO_FIX,
        db_index=True,
    )
    distance_from_household_metres = models.FloatField(null=True, blank=True)
    location_verified_at = models.DateTimeField(null=True, blank=True)

    flagged_for_review = models.BooleanField(default=False, db_index=True)
    review_reason = models.CharField(max_length=200, blank=True)
    reviewed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_visits",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_outcome = models.CharField(
        max_length=200,
        blank=True,
        help_text="What the supervisor found. An unverified visit is usually genuine.",
    )

    class Meta:
        ordering = ["-visit_date", "-created_at"]
        indexes = [
            models.Index(fields=["mentor_mother", "visit_date"]),
            models.Index(fields=["client", "visit_date"]),
            models.Index(fields=["location_status", "visit_date"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(latitude__gte=-90, latitude__lte=90)
                | models.Q(latitude__isnull=True),
                name="visit_latitude_in_range",
            ),
            models.CheckConstraint(
                condition=models.Q(longitude__gte=-180, longitude__lte=180)
                | models.Q(longitude__isnull=True),
                name="visit_longitude_in_range",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.client_id} on {self.visit_date}"

    @property
    def is_location_verified(self) -> bool:
        return self.location_status == self.LocationStatus.VERIFIED

    def evaluate_location(self, *, save: bool = True) -> str:
        """
        Compare the reported position against the registered household point
        and record the verdict.

        Called on the server when the record arrives. Never called on a handset.
        """
        cfg = settings.PROGRAMME
        radius = cfg["LOCATION_MATCH_RADIUS_METRES"]
        max_accuracy = cfg["LOCATION_MAX_ACCURACY_METRES"]

        if self.latitude is None or self.longitude is None:
            self.location_status = self.LocationStatus.NO_FIX
            self.distance_from_household_metres = None
        elif (
            self.client.household_latitude is None
            or self.client.household_longitude is None
        ):
            self.location_status = self.LocationStatus.NO_REFERENCE
            self.distance_from_household_metres = None
        elif (
            self.location_accuracy_metres is not None
            and self.location_accuracy_metres > max_accuracy
        ):
            self.location_status = self.LocationStatus.LOW_ACCURACY
        else:
            distance = haversine_metres(
                float(self.latitude),
                float(self.longitude),
                float(self.client.household_latitude),
                float(self.client.household_longitude),
            )
            self.distance_from_household_metres = round(distance, 1)
            self.location_status = (
                self.LocationStatus.VERIFIED
                if distance <= radius
                else self.LocationStatus.OUT_OF_RANGE
            )

        if self.location_status == self.LocationStatus.VERIFIED:
            self.location_verified_at = timezone.now()
        elif self.location_status == self.LocationStatus.OUT_OF_RANGE:
            self.flagged_for_review = True
            self.review_reason = (
                f"Reported position is "
                f"{self.distance_from_household_metres:.0f} m from the "
                f"registered household. Confirm the household point is correct."
            )

        if save:
            self.save(
                update_fields=[
                    "location_status",
                    "distance_from_household_metres",
                    "location_verified_at",
                    "flagged_for_review",
                    "review_reason",
                    "updated_at",
                ]
            )
        return self.location_status


class GeospatialAnomaly(BaseModel):
    """
    An anomaly raised by the server-side geospatial detection job.

    The job looks for patterns across many visits that no single record shows:
    two visits far apart within an impossible travel time, several visits
    reporting an identical position to six decimal places, a whole day of
    visits recorded from one point.

    An anomaly is a prompt for a supervisor conversation. It is not a finding of
    misconduct, and the record makes that distinction explicit so that a
    dashboard cannot present it as one.
    """

    class Kind(models.TextChoices):
        IMPOSSIBLE_TRAVEL = "IMPOSSIBLE_TRAVEL", "Travel between visits was too fast"
        IDENTICAL_POSITION = "IDENTICAL_POSITION", "Several visits share one position"
        CLUSTERED_DAY = "CLUSTERED_DAY", "A day of visits from a single point"
        BULK_BACKDATED = "BULK_BACKDATED", "Many records entered long after the date"

    class Disposition(models.TextChoices):
        OPEN = "OPEN", "Awaiting supervisor review"
        EXPLAINED = "EXPLAINED", "Reviewed, an innocent explanation was found"
        DATA_CORRECTED = "DATA_CORRECTED", "Reviewed, the underlying data was wrong"
        ESCALATED = "ESCALATED", "Referred to the programme manager"

    mentor_mother = models.ForeignKey(
        "registry.MentorMother", on_delete=models.CASCADE, related_name="anomalies"
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    detected_for_date = models.DateField(db_index=True)
    visits = models.ManyToManyField(HomeVisit, related_name="anomalies", blank=True)

    detail = models.TextField(help_text="What the detection job measured.")
    confidence = models.CharField(
        max_length=8,
        choices=[("LOW", "Low"), ("MEDIUM", "Medium"), ("HIGH", "High")],
        default="LOW",
    )

    disposition = models.CharField(
        max_length=16, choices=Disposition.choices, default=Disposition.OPEN
    )
    reviewed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_anomalies",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-detected_for_date"]
        verbose_name_plural = "geospatial anomalies"
        indexes = [models.Index(fields=["mentor_mother", "disposition"])]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} on {self.detected_for_date}"


class SyncBatch(BaseModel):
    """
    One synchronisation attempt from one handset.

    The batch record is how the programme sees where data is stuck. A handset
    that has not synchronised for days is holding visit records that no
    supervisor can act on, and that is an operational problem well before it
    becomes a data quality problem.
    """

    class Status(models.TextChoices):
        ACCEPTED = "ACCEPTED", "All records accepted"
        PARTIAL = "PARTIAL", "Some records were rejected"
        REJECTED = "REJECTED", "The batch was rejected"

    device_id = models.CharField(max_length=64, db_index=True)
    user = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="sync_batches"
    )
    status = models.CharField(max_length=10, choices=Status.choices)

    records_submitted = models.PositiveIntegerField(default=0)
    records_accepted = models.PositiveIntegerField(default=0)
    records_duplicate = models.PositiveIntegerField(default=0)
    records_rejected = models.PositiveIntegerField(default=0)
    rejection_detail = models.JSONField(default=list, blank=True)

    oldest_record_created_at = models.DateTimeField(null=True, blank=True)
    app_version = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "sync batches"

    def __str__(self) -> str:
        return f"{self.device_id} at {self.created_at:%Y-%m-%d %H:%M}"

    @property
    def backlog_hours(self) -> float | None:
        if not self.oldest_record_created_at:
            return None
        delta = self.created_at - self.oldest_record_created_at
        return delta.total_seconds() / 3600
