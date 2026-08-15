"""
Abstract base models shared by every application.

Three behaviours are supplied here.

TimeStampedModel records when a row was created and last changed.

UUIDModel gives every row a UUID primary key. A UUID is safe to generate on a
handset that is offline, so the mobile client can create a record and keep the
same identifier after the record synchronises. A sequential integer key cannot
do this.

SoftDeleteModel marks a row as removed instead of deleting it. Clinical records
must stay recoverable for the retention period stated in the data protection
annex, and an audit needs to see that a row once existed.
"""

from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class SoftDeleteQuerySet(models.QuerySet):
    def alive(self) -> "SoftDeleteQuerySet":
        return self.filter(deleted_at__isnull=True)

    def dead(self) -> "SoftDeleteQuerySet":
        return self.filter(deleted_at__isnull=False)

    def delete(self):  # type: ignore[override]
        return self.update(deleted_at=timezone.now())


class SoftDeleteManager(models.Manager.from_queryset(SoftDeleteQuerySet)):  # type: ignore[misc]
    def get_queryset(self) -> SoftDeleteQuerySet:
        return super().get_queryset().filter(deleted_at__isnull=True)


class SoftDeleteModel(models.Model):
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    deleted_reason = models.CharField(max_length=200, blank=True)

    objects = SoftDeleteManager()
    all_objects = models.Manager.from_queryset(SoftDeleteQuerySet)()

    class Meta:
        abstract = True

    def delete(self, using=None, keep_parents=False, reason: str = ""):  # type: ignore[override]
        self.deleted_at = timezone.now()
        self.deleted_reason = reason
        self.save(update_fields=["deleted_at", "deleted_reason", "updated_at"])

    def restore(self) -> None:
        self.deleted_at = None
        self.deleted_reason = ""
        self.save(update_fields=["deleted_at", "deleted_reason", "updated_at"])


class BaseModel(UUIDModel, TimeStampedModel, SoftDeleteModel):
    """The default base for every record that holds programme data."""

    class Meta:
        abstract = True


class SyncableModel(BaseModel):
    """
    Base for any record that a handset can create while it is offline.

    The mobile client sets id, client_created_at and device_id. The server sets
    server_received_at. Three fields make the synchronisation safe.

    A repeated upload of the same id is ignored, so a retry after a dropped
    connection cannot create a duplicate visit.

    sync_version increases on every server-side write. The client sends the
    version it holds. If the versions differ, the server rejects the write and
    the client must fetch the current row first.

    client_created_at is the time the handset recorded, which can be days
    earlier than server_received_at in an area with no coverage. Reports use
    client_created_at. Operational alarms use the gap between the two.
    """

    device_id = models.CharField(
        max_length=64,
        blank=True,
        help_text="Installation identifier of the handset that created the record.",
    )
    client_created_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Time recorded on the handset. Use this for programme reports.",
    )
    server_received_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Time the record reached the server.",
    )
    sync_version = models.PositiveIntegerField(default=1)

    class Meta:
        abstract = True

    @property
    def sync_delay_hours(self) -> float | None:
        """Hours the record spent on the handset before it synchronised."""
        if not (self.client_created_at and self.server_received_at):
            return None
        delta = self.server_received_at - self.client_created_at
        return delta.total_seconds() / 3600
