"""Serializers for the supervisor's view of visits. Creation happens only
through sync, where the location verdict is computed on arrival; nothing here
can write a verdict field."""

from __future__ import annotations

from rest_framework import serializers

from apps.common.serializers import IdentifierGatedSerializerMixin

from .models import GeospatialAnomaly, HomeVisit, SyncBatch


class HomeVisitSerializer(IdentifierGatedSerializerMixin, serializers.ModelSerializer):
    # The reported position locates a household as surely as an address does,
    # so it is gated with the identifiers. The verdict and the distance are
    # not: they say how far a reading was, not where it was.
    identifier_fields = ("latitude", "longitude")

    client = serializers.SlugRelatedField(slug_field="client_code", read_only=True)
    infant = serializers.SlugRelatedField(slug_field="baby_code", read_only=True)
    mentor_mother = serializers.SlugRelatedField(
        slug_field="staff_code", read_only=True
    )
    reviewed_by = serializers.SlugRelatedField(slug_field="username", read_only=True)

    class Meta:
        model = HomeVisit
        fields = [
            "id", "client", "infant", "mentor_mother", "purpose", "result",
            "visit_date", "duration_minutes", "notes",
            "latitude", "longitude", "location_accuracy_metres",
            "location_captured_at", "location_status",
            "distance_from_household_metres", "location_verified_at",
            "flagged_for_review", "review_reason",
            "reviewed_by", "reviewed_at", "review_outcome",
            "client_created_at", "server_received_at", "device_id",
            "created_at",
        ]
        read_only_fields = fields


class VisitReviewSerializer(serializers.Serializer):
    review_outcome = serializers.CharField(max_length=200)


class GeospatialAnomalySerializer(serializers.ModelSerializer):
    mentor_mother = serializers.SlugRelatedField(
        slug_field="staff_code", read_only=True
    )
    reviewed_by = serializers.SlugRelatedField(slug_field="username", read_only=True)

    class Meta:
        model = GeospatialAnomaly
        fields = [
            "id", "mentor_mother", "kind", "detected_for_date", "visits",
            "detail", "confidence", "disposition",
            "reviewed_by", "reviewed_at", "review_note", "created_at",
        ]
        read_only_fields = fields


class AnomalyDispositionSerializer(serializers.Serializer):
    """OPEN is not settable: it is the starting state, not a finding."""

    disposition = serializers.ChoiceField(
        choices=[
            (GeospatialAnomaly.Disposition.EXPLAINED, "Explained"),
            (GeospatialAnomaly.Disposition.DATA_CORRECTED, "Data corrected"),
            (GeospatialAnomaly.Disposition.ESCALATED, "Escalated"),
        ]
    )
    review_note = serializers.CharField(max_length=2000)


class SyncBatchSerializer(serializers.ModelSerializer):
    user = serializers.SlugRelatedField(slug_field="username", read_only=True)
    backlog_hours = serializers.FloatField(read_only=True)

    class Meta:
        model = SyncBatch
        fields = [
            "id", "device_id", "user", "status",
            "records_submitted", "records_accepted", "records_duplicate",
            "records_rejected", "oldest_record_created_at", "app_version",
            "backlog_hours", "created_at",
        ]
        read_only_fields = fields
