"""
Early infant diagnosis: appointments, samples, results and treatment linkage.

This module holds the measurement the pilot exists to change.

The gap the proposal documents is between an infant testing positive and that
infant starting treatment. In a paper system the delay is invisible while it is
happening. A result reaches a facility, a register is filled in, and nothing
prompts anyone until someone reviews the register. By then weeks have passed.

Every step below therefore carries its own timestamp. The delay is not derived
from one date at the end. It is decomposed, so that when the pilot reports a
change, the report can say which link in the chain moved.

    sample collected
      -> dispatched to the laboratory
      -> result issued by the laboratory
      -> result entered in the system
      -> supervisor acknowledged the result
      -> caregiver informed
      -> infant started treatment

A delay between dispatch and result is a laboratory problem. This application
cannot fix it. A delay between result entry and acknowledgement is a relay
problem. That is the delay this application exists to remove. Keeping the two
apart is what stops the pilot from claiming credit it has not earned.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel


class EidAppointment(BaseModel):
    """A scheduled infant test, positioned by the national testing schedule."""

    class Milestone(models.TextChoices):
        BIRTH = "BIRTH", "At birth"
        WEEK_6 = "WEEK_6", "Six weeks"
        MONTH_9 = "MONTH_9", "Nine months"
        MONTH_18 = "MONTH_18", "Eighteen months"
        UNSCHEDULED = "UNSCHEDULED", "Unscheduled or repeat test"

    class Status(models.TextChoices):
        SCHEDULED = "SCHEDULED", "Scheduled"
        ATTENDED = "ATTENDED", "Attended, sample collected"
        MISSED = "MISSED", "Missed"
        RESCHEDULED = "RESCHEDULED", "Rescheduled"
        CANCELLED = "CANCELLED", "Cancelled"

    infant = models.ForeignKey(
        "registry.Infant", on_delete=models.PROTECT, related_name="eid_appointments"
    )
    milestone = models.CharField(max_length=12, choices=Milestone.choices)
    due_date = models.DateField(db_index=True)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.SCHEDULED, db_index=True
    )
    attended_date = models.DateField(null=True, blank=True)

    reminder_sent_at = models.DateTimeField(null=True, blank=True)
    reminder_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["due_date"]
        unique_together = [("infant", "milestone")]
        indexes = [models.Index(fields=["status", "due_date"])]

    def __str__(self) -> str:
        return f"{self.infant.baby_code} {self.get_milestone_display()}"

    @property
    def days_overdue(self) -> int:
        if self.status != self.Status.SCHEDULED:
            return 0
        return max(0, (timezone.localdate() - self.due_date).days)

    @classmethod
    def build_schedule(cls, infant) -> list["EidAppointment"]:
        """
        Create the full appointment schedule for an infant from the date of
        birth and the national testing intervals.
        """
        weeks = settings.PROGRAMME["EID_SCHEDULE_WEEKS"]
        created = []
        for milestone, offset in weeks.items():
            appointment, was_created = cls.objects.get_or_create(
                infant=infant,
                milestone=milestone,
                defaults={"due_date": infant.date_of_birth + timedelta(weeks=offset)},
            )
            if was_created:
                created.append(appointment)
        return created


class EidSample(BaseModel):
    """
    A dried blood spot sample and the result that comes back from it.

    Clinical fields on this model are writable only by a user whose role is in
    CLINICAL_DATA_ROLES. A mentor mother cannot record a laboratory result.
    """

    class Result(models.TextChoices):
        NEGATIVE = "NEGATIVE", "HIV not detected"
        POSITIVE = "POSITIVE", "HIV detected"
        INDETERMINATE = "INDETERMINATE", "Indeterminate, repeat required"
        REJECTED = "REJECTED", "Sample rejected by the laboratory"
        PENDING = "PENDING", "Awaiting a result"

    appointment = models.OneToOneField(
        EidAppointment, on_delete=models.PROTECT, related_name="sample"
    )
    sample_identifier = models.CharField(max_length=40, unique=True)

    # -- Timestamps that decompose the delay -------------------------------
    collected_on = models.DateField(db_index=True)
    dispatched_on = models.DateField(null=True, blank=True)
    laboratory_received_on = models.DateField(null=True, blank=True)
    result_issued_on = models.DateField(
        null=True, blank=True, help_text="Date the laboratory issued the result."
    )
    result_entered_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Time a supervisor entered the result in this system.",
    )
    result_acknowledged_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "Time a supervisor confirmed the result and started acting on it. "
            "The gap from entry to acknowledgement is the relay delay."
        ),
    )
    caregiver_informed_at = models.DateTimeField(null=True, blank=True)

    result = models.CharField(
        max_length=14, choices=Result.choices, default=Result.PENDING, db_index=True
    )
    laboratory_name = models.CharField(max_length=120, blank=True)

    entered_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="entered_samples",
    )
    acknowledged_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="acknowledged_samples",
    )

    class Meta:
        ordering = ["-collected_on"]
        indexes = [
            models.Index(fields=["result", "result_entered_at"]),
            models.Index(fields=["result", "result_acknowledged_at"]),
        ]

    def __str__(self) -> str:
        return self.sample_identifier

    # -- Derived intervals -------------------------------------------------

    @property
    def days_collection_to_result(self) -> int | None:
        """Laboratory turnaround. Outside the control of this application."""
        if self.result_issued_on and self.collected_on:
            return (self.result_issued_on - self.collected_on).days
        return None

    @property
    def hours_entry_to_acknowledgement(self) -> float | None:
        """
        The relay delay. This is the interval the intervention targets.

        A null value on a positive result means nobody has acknowledged it yet,
        which is the condition that should be raising an alert.
        """
        if self.result_entered_at and self.result_acknowledged_at:
            delta = self.result_acknowledged_at - self.result_entered_at
            return delta.total_seconds() / 3600
        return None

    @property
    def is_unacknowledged_positive(self) -> bool:
        return self.result == self.Result.POSITIVE and self.result_acknowledged_at is None


class ArtLinkage(BaseModel):
    """
    The record of an infant starting antiretroviral treatment.

    days_to_linkage is the primary outcome indicator. It is measured from the
    date the result was issued by the laboratory, not from the date the result
    was entered in this system. Measuring from the entry date would hide any
    delay in getting the result into the system, and would make the
    intervention look better than it is.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", "Not yet started"
        STARTED = "STARTED", "Treatment started"
        REFUSED = "REFUSED", "Caregiver declined"
        UNREACHABLE = "UNREACHABLE", "Caregiver could not be reached"
        TRANSFERRED = "TRANSFERRED", "Referred to another facility"
        DECEASED = "DECEASED", "Infant died before starting treatment"

    infant = models.OneToOneField(
        "registry.Infant", on_delete=models.PROTECT, related_name="art_linkage"
    )
    triggering_sample = models.ForeignKey(
        EidSample,
        on_delete=models.PROTECT,
        related_name="linkages",
        help_text="The positive result that started this linkage.",
    )

    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    art_start_date = models.DateField(null=True, blank=True, db_index=True)
    regimen = models.CharField(max_length=80, blank=True)
    treating_facility = models.ForeignKey(
        "registry.Facility",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="art_linkages",
    )

    barrier_note = models.TextField(
        blank=True,
        help_text="Why linkage has not happened. Read at the monthly review.",
    )
    recorded_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="recorded_linkages",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "ART linkage"

    def __str__(self) -> str:
        return f"{self.infant.baby_code}: {self.get_status_display()}"

    @property
    def days_to_linkage(self) -> int | None:
        """Days from the laboratory issuing the result to treatment starting."""
        issued = self.triggering_sample.result_issued_on
        if self.art_start_date and issued:
            return (self.art_start_date - issued).days
        return None

    @property
    def days_outstanding(self) -> int | None:
        """Days a pending linkage has been open. Drives alert escalation."""
        if self.status != self.Status.PENDING:
            return None
        issued = self.triggering_sample.result_issued_on
        if not issued:
            return None
        return (timezone.localdate() - issued).days
