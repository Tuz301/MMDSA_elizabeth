"""
The alert engine.

Every rule here is deterministic. A condition is either met or it is not, and a
supervisor can read the rule and predict what the system will do. No model
scores or ranks anything during the pilot.

That is a deliberate constraint, not a limitation of effort. The pilot has to
show that removing relay delay changes the linkage outcome. If a model decided
which alerts to raise, a change in outcome could not be separated from a change
in the model, and the evaluation would prove nothing. Predictive scoring is
scoped for the phase after the pilot, once the deterministic baseline exists to
compare against.

Every alert has a deadline and an escalation path. An alert that nobody answers
must reach someone senior on its own. An unanswered alert is the failure mode
the paper system already has, and reproducing it in software would be no gain
at all.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel


class AlertType(models.TextChoices):
    """Every condition the engine watches for."""

    POSITIVE_RESULT_UNACKNOWLEDGED = (
        "POS_UNACK",
        "A positive infant result has not been acknowledged",
    )
    ART_LINKAGE_OVERDUE = (
        "LINK_OVERDUE",
        "A positive infant has not started treatment",
    )
    EID_APPOINTMENT_DUE = "EID_DUE", "An infant test appointment falls due"
    EID_APPOINTMENT_MISSED = "EID_MISSED", "An infant test appointment was missed"
    SAMPLE_RESULT_OVERDUE = (
        "SAMPLE_OVERDUE",
        "A sample has been at the laboratory too long",
    )
    CLIENT_MISSED_VISIT = "VISIT_MISSED", "A planned home visit did not happen"
    CLIENT_AT_RISK_LTFU = "LTFU_RISK", "A client has had no contact for some time"
    VISIT_FLAGGED_FOR_REVIEW = "VISIT_FLAG", "A visit needs supervisor review"
    SYNC_BACKLOG = "SYNC_BACKLOG", "A handset has not synchronised"


class Severity(models.TextChoices):
    CRITICAL = "CRITICAL", "Critical"
    HIGH = "HIGH", "High"
    MEDIUM = "MEDIUM", "Medium"
    LOW = "LOW", "Low"


class AlertRule(BaseModel):
    """
    A configurable rule. Thresholds are data, not code.

    A programme manager can change a threshold after a review without a code
    release. Every change is recorded in the audit log, so an evaluator can see
    what the thresholds were on any given date.
    """

    alert_type = models.CharField(max_length=16, choices=AlertType.choices, unique=True)
    is_enabled = models.BooleanField(default=True)
    severity = models.CharField(max_length=8, choices=Severity.choices)

    threshold_value = models.IntegerField(
        help_text="Meaning depends on the rule: days overdue, hours elapsed, count."
    )
    threshold_unit = models.CharField(
        max_length=10,
        choices=[("HOURS", "Hours"), ("DAYS", "Days"), ("COUNT", "Count")],
        default="DAYS",
    )

    notify_mentor_mother = models.BooleanField(default=False)
    notify_facility_supervisor = models.BooleanField(default=True)
    notify_lga_coordinator = models.BooleanField(default=False)

    send_sms = models.BooleanField(default=False)
    sms_template_key = models.CharField(max_length=40, blank=True)

    escalate_after_hours = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Hours without acknowledgement before the alert goes one tier up.",
    )

    class Meta:
        ordering = ["alert_type"]

    def __str__(self) -> str:
        return f"{self.get_alert_type_display()} ({self.severity})"


class Alert(BaseModel):
    """
    One raised alert.

    subject_type and subject_id point at the thing the alert concerns: an
    infant, a sample, a client, a visit. A generic reference is used rather than
    a foreign key per type, because a new alert type must not require a schema
    change.

    deduplication_key stops the engine raising the same alert every time it
    runs. The key combines the alert type with the subject and, where relevant,
    the period. An engine that re-raises alerts trains supervisors to ignore
    them, and an ignored alert is worse than no alert.
    """

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        ACKNOWLEDGED = "ACKNOWLEDGED", "Acknowledged"
        RESOLVED = "RESOLVED", "Resolved"
        ESCALATED = "ESCALATED", "Escalated"
        CANCELLED = "CANCELLED", "No longer applicable"

    rule = models.ForeignKey(
        AlertRule, on_delete=models.PROTECT, related_name="alerts"
    )
    alert_type = models.CharField(
        max_length=16, choices=AlertType.choices, db_index=True
    )
    severity = models.CharField(max_length=8, choices=Severity.choices, db_index=True)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.OPEN, db_index=True
    )

    facility = models.ForeignKey(
        "registry.Facility", on_delete=models.PROTECT, related_name="alerts"
    )
    assigned_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_alerts",
    )
    assigned_mentor_mother = models.ForeignKey(
        "registry.MentorMother",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_alerts",
    )

    subject_type = models.CharField(
        max_length=40, help_text="Model label, for example registry.Infant."
    )
    subject_id = models.UUIDField()

    deduplication_key = models.CharField(max_length=200, unique=True, db_index=True)

    # The message shown in the application. It may name a person, because the
    # application is behind authentication. The SMS body is separate and never
    # names anyone.
    title = models.CharField(max_length=160)
    detail = models.TextField(blank=True)

    raised_at = models.DateTimeField(default=timezone.now, db_index=True)
    acknowledge_by = models.DateTimeField(
        db_index=True, help_text="Deadline. Set from the severity."
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="acknowledged_alerts",
    )
    acknowledgement_channel = models.CharField(
        max_length=10,
        blank=True,
        choices=[("APP", "In the application"), ("SMS", "By SMS reply")],
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.TextField(blank=True)

    escalation_level = models.PositiveSmallIntegerField(
        default=0, help_text="0 means not escalated. Each step raises the tier."
    )
    escalated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-severity", "acknowledge_by"]
        indexes = [
            models.Index(fields=["status", "acknowledge_by"]),
            models.Index(fields=["facility", "status", "severity"]),
            models.Index(fields=["subject_type", "subject_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_alert_type_display()} [{self.status}]"

    def save(self, *args, **kwargs):
        if not self.acknowledge_by:
            hours = settings.PROGRAMME["ALERT_SLA_HOURS"].get(self.severity, 72)
            self.acknowledge_by = self.raised_at + timedelta(hours=hours)
        super().save(*args, **kwargs)

    # -- State -------------------------------------------------------------

    @property
    def is_past_deadline(self) -> bool:
        return (
            self.status in {self.Status.OPEN, self.Status.ESCALATED}
            and timezone.now() > self.acknowledge_by
        )

    @property
    def hours_to_acknowledgement(self) -> float | None:
        if not self.acknowledged_at:
            return None
        return (self.acknowledged_at - self.raised_at).total_seconds() / 3600

    def acknowledge(self, *, user=None, channel: str = "APP") -> None:
        if self.status in {self.Status.RESOLVED, self.Status.CANCELLED}:
            return
        self.status = self.Status.ACKNOWLEDGED
        self.acknowledged_at = timezone.now()
        self.acknowledged_by = user
        self.acknowledgement_channel = channel
        self.save(
            update_fields=[
                "status",
                "acknowledged_at",
                "acknowledged_by",
                "acknowledgement_channel",
                "updated_at",
            ]
        )

    def resolve(self, note: str = "") -> None:
        self.status = self.Status.RESOLVED
        self.resolved_at = timezone.now()
        self.resolution_note = note
        self.save(
            update_fields=["status", "resolved_at", "resolution_note", "updated_at"]
        )


class AlertEscalation(BaseModel):
    """One escalation step. Kept as a record so the path can be audited."""

    alert = models.ForeignKey(
        Alert, on_delete=models.CASCADE, related_name="escalations"
    )
    level = models.PositiveSmallIntegerField()
    escalated_to = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="escalations_received"
    )
    reason = models.CharField(max_length=200)

    class Meta:
        ordering = ["alert", "level"]
        unique_together = [("alert", "level")]

    def __str__(self) -> str:
        return f"{self.alert_id} to level {self.level}"
