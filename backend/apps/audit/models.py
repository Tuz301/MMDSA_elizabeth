"""
The audit trail.

The compliance annex commits to a record of who accessed what and when. That
commitment is only meaningful if reading is logged, not just writing. A data
breach in a health programme is usually somebody reading records they had no
reason to read, and a write-only audit log cannot see that at all.

Two rules govern this table.

An audit record is never changed and never deleted by the application. The
instance role holds s3:PutObject on the audit bucket and nothing else, so the
archive cannot be rewritten even if the application is compromised.

An audit record never contains the value that was read. It records that a user
read a named field on a named row. Storing the value would make the audit log a
second, less protected copy of the patient register.
"""

from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone


class AuditAction(models.TextChoices):
    CREATE = "CREATE", "Created a record"
    READ = "READ", "Read a record"
    UPDATE = "UPDATE", "Changed a record"
    SOFT_DELETE = "SOFT_DELETE", "Marked a record as removed"
    EXPORT = "EXPORT", "Exported data"
    LOGIN = "LOGIN", "Signed in"
    LOGIN_FAILED = "LOGIN_FAILED", "Sign in failed"
    LOGOUT = "LOGOUT", "Signed out"
    PERMISSION_DENIED = "PERMISSION_DENIED", "Access was refused"
    CONFIG_CHANGE = "CONFIG_CHANGE", "Changed a rule or a threshold"
    IDENTIFIER_REVEALED = "IDENTIFIER_REVEALED", "Read a name, number or address"


class DataClassification(models.TextChoices):
    """The five tiers from the data classification matrix."""

    TIER_1_DIRECT_IDENTIFIER = "T1", "Tier 1, direct identifier"
    TIER_2_CLINICAL = "T2", "Tier 2, clinical data"
    TIER_3_PROGRAMME = "T3", "Tier 3, programme data"
    TIER_4_OPERATIONAL = "T4", "Tier 4, operational data"
    TIER_5_PUBLIC = "T5", "Tier 5, non-sensitive"


class AuditLog(models.Model):
    """
    One audited event.

    This model does not inherit from BaseModel. It has no updated_at, because
    nothing updates it, and no deleted_at, because nothing deletes it.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)

    actor = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="audit_entries",
    )
    actor_username = models.CharField(
        max_length=150,
        blank=True,
        help_text="Copied at write time, so the record survives a renamed account.",
    )
    actor_role = models.CharField(max_length=24, blank=True)

    action = models.CharField(max_length=20, choices=AuditAction.choices, db_index=True)
    classification = models.CharField(
        max_length=2, choices=DataClassification.choices, db_index=True
    )

    object_type = models.CharField(max_length=60, blank=True, db_index=True)
    object_id = models.CharField(max_length=64, blank=True, db_index=True)
    object_reference = models.CharField(
        max_length=40,
        blank=True,
        help_text="Programme code of the subject, for example a Baby Code.",
    )

    fields_touched = models.JSONField(
        default=list,
        blank=True,
        help_text="Field names only. Never the values held in those fields.",
    )

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    request_path = models.CharField(max_length=200, blank=True)
    request_method = models.CharField(max_length=8, blank=True)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)

    justification = models.CharField(
        max_length=200,
        blank=True,
        help_text="Required when a user reads a record outside their usual caseload.",
    )

    archived_to_s3 = models.BooleanField(default=False, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["actor", "occurred_at"]),
            models.Index(fields=["object_type", "object_id", "occurred_at"]),
            models.Index(fields=["action", "classification", "occurred_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.actor_username} {self.action} {self.object_type} at {self.occurred_at:%Y-%m-%d %H:%M}"

    def save(self, *args, **kwargs):
        if self.pk and AuditLog.objects.filter(pk=self.pk).exists():
            raise PermissionError(
                "An audit record cannot be changed after it has been written."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("An audit record cannot be deleted.")


class RetentionRun(models.Model):
    """
    A record of a scheduled retention job.

    The compliance annex sets retention periods. A period that is written down
    but never applied is worse than no policy, because it states a commitment
    the programme is not keeping. This table is the evidence that the job ran
    and what it did.
    """

    class Outcome(models.TextChoices):
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"
        DRY_RUN = "DRY_RUN", "Dry run, nothing was changed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(max_length=10, choices=Outcome.choices)

    policy_name = models.CharField(max_length=80)
    records_examined = models.PositiveIntegerField(default=0)
    records_anonymised = models.PositiveIntegerField(default=0)
    records_purged = models.PositiveIntegerField(default=0)
    error_detail = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"{self.policy_name} on {self.started_at:%Y-%m-%d}"
