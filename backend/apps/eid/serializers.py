"""
EID serializers.

Every timestamp in the relay chain is server-set. The API never accepts
result_entered_at, result_acknowledged_at or caregiver_informed_at from a
request body, because the gap between entry and acknowledgement is the relay
delay — the pilot's primary process indicator — and a client-supplied
timestamp would let that measurement be falsified.
"""

from __future__ import annotations

from django.utils import timezone
from rest_framework import serializers

from apps.common.serializers import ScopedPrimaryKeyRelatedField, ScopedSlugRelatedField
from apps.registry.models import Facility, Infant

from .models import ArtLinkage, EidAppointment, EidSample


class EidAppointmentSerializer(serializers.ModelSerializer):
    infant = ScopedSlugRelatedField(
        slug_field="baby_code", queryset=Infant.objects.all()
    )
    days_overdue = serializers.IntegerField(read_only=True)

    class Meta:
        model = EidAppointment
        fields = [
            "id", "infant", "milestone", "due_date", "status", "attended_date",
            "days_overdue", "reminder_sent_at", "reminder_count",
            "created_at", "updated_at",
        ]
        # Status moves only through the attended / missed / reschedule
        # actions. Milestone appointments come from build_schedule; create
        # exists for an unscheduled repeat test.
        read_only_fields = [
            "id", "status", "attended_date", "days_overdue",
            "reminder_sent_at", "reminder_count", "created_at", "updated_at",
        ]


class AppointmentAttendedSerializer(serializers.Serializer):
    attended_date = serializers.DateField()

    def validate_attended_date(self, value):
        if value > timezone.localdate():
            raise serializers.ValidationError(
                "An attendance date cannot be in the future."
            )
        return value


class AppointmentRescheduleSerializer(serializers.Serializer):
    due_date = serializers.DateField()


class EidSampleSerializer(serializers.ModelSerializer):
    appointment = ScopedPrimaryKeyRelatedField(queryset=EidAppointment.objects.all())
    baby_code = serializers.CharField(
        source="appointment.infant.baby_code", read_only=True
    )
    entered_by = serializers.SlugRelatedField(slug_field="username", read_only=True)
    acknowledged_by = serializers.SlugRelatedField(
        slug_field="username", read_only=True
    )
    days_collection_to_result = serializers.IntegerField(read_only=True)
    hours_entry_to_acknowledgement = serializers.FloatField(read_only=True)
    is_unacknowledged_positive = serializers.BooleanField(read_only=True)

    class Meta:
        model = EidSample
        fields = [
            "id", "appointment", "baby_code", "sample_identifier",
            "collected_on", "dispatched_on", "laboratory_received_on",
            "laboratory_name",
            "result", "result_issued_on", "result_entered_at",
            "result_acknowledged_at", "caregiver_informed_at",
            "entered_by", "acknowledged_by",
            "days_collection_to_result", "hours_entry_to_acknowledgement",
            "is_unacknowledged_positive", "created_at", "updated_at",
        ]
        # The result and its timestamps move only through the result and
        # acknowledge actions.
        read_only_fields = [
            "id", "baby_code", "result", "result_issued_on", "result_entered_at",
            "result_acknowledged_at", "caregiver_informed_at",
            "entered_by", "acknowledged_by",
            "days_collection_to_result", "hours_entry_to_acknowledgement",
            "is_unacknowledged_positive", "created_at", "updated_at",
        ]

    def validate_collected_on(self, value):
        if value > timezone.localdate():
            raise serializers.ValidationError(
                "A collection date cannot be in the future."
            )
        return value

    def validate(self, attrs):
        collected_on = attrs.get(
            "collected_on", self.instance.collected_on if self.instance else None
        )
        dispatched_on = attrs.get(
            "dispatched_on", self.instance.dispatched_on if self.instance else None
        )
        if collected_on and dispatched_on and dispatched_on < collected_on:
            raise serializers.ValidationError(
                {"dispatched_on": "A sample cannot be dispatched before it is collected."}
            )

        if self.instance is None:
            appointment = attrs["appointment"]
            if appointment.status not in {
                EidAppointment.Status.SCHEDULED,
                EidAppointment.Status.RESCHEDULED,
                EidAppointment.Status.ATTENDED,
            }:
                raise serializers.ValidationError(
                    {"appointment": (
                        "A sample cannot be registered against a missed or "
                        "cancelled appointment. Reschedule it first."
                    )}
                )
            if hasattr(appointment, "sample"):
                raise serializers.ValidationError(
                    {"appointment": "This appointment already has a sample."}
                )
        return attrs


class SampleResultSerializer(serializers.Serializer):
    """The result entry action. PENDING is not enterable: it is the absence
    of a result, not a result."""

    result = serializers.ChoiceField(
        choices=[
            (EidSample.Result.NEGATIVE, "HIV not detected"),
            (EidSample.Result.POSITIVE, "HIV detected"),
            (EidSample.Result.INDETERMINATE, "Indeterminate, repeat required"),
            (EidSample.Result.REJECTED, "Sample rejected by the laboratory"),
        ]
    )
    result_issued_on = serializers.DateField()
    laboratory_name = serializers.CharField(
        max_length=120, required=False, allow_blank=True
    )

    def validate_result_issued_on(self, value):
        if value > timezone.localdate():
            raise serializers.ValidationError(
                "The laboratory cannot have issued a result in the future."
            )
        return value


class ArtLinkageSerializer(serializers.ModelSerializer):
    infant = ScopedSlugRelatedField(
        slug_field="baby_code", queryset=Infant.objects.all()
    )
    triggering_sample = ScopedPrimaryKeyRelatedField(
        queryset=EidSample.objects.all()
    )
    treating_facility = ScopedPrimaryKeyRelatedField(
        queryset=Facility.objects.all(), required=False, allow_null=True
    )
    recorded_by = serializers.SlugRelatedField(slug_field="username", read_only=True)
    days_to_linkage = serializers.IntegerField(read_only=True)
    days_outstanding = serializers.IntegerField(read_only=True)

    class Meta:
        model = ArtLinkage
        fields = [
            "id", "infant", "triggering_sample", "status", "art_start_date",
            "regimen", "treating_facility", "barrier_note", "recorded_by",
            "days_to_linkage", "days_outstanding", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "recorded_by", "days_to_linkage", "days_outstanding",
            "created_at", "updated_at",
        ]

    def validate(self, attrs):
        instance = self.instance
        infant = attrs.get("infant", instance.infant if instance else None)
        sample = attrs.get(
            "triggering_sample", instance.triggering_sample if instance else None
        )
        linkage_status = attrs.get("status", instance.status if instance else None)
        art_start_date = attrs.get(
            "art_start_date", instance.art_start_date if instance else None
        )
        barrier_note = attrs.get(
            "barrier_note", instance.barrier_note if instance else ""
        )

        if instance is not None:
            # The linkage's identity is fixed at creation. Re-pointing it at
            # another infant or sample would rewrite the outcome record.
            for frozen in ("infant", "triggering_sample"):
                if frozen in attrs and getattr(attrs[frozen], "pk", None) != getattr(
                    getattr(instance, frozen), "pk", None
                ):
                    raise serializers.ValidationError(
                        {frozen: "This field is fixed once the linkage exists."}
                    )

        if sample is not None:
            if sample.result != EidSample.Result.POSITIVE:
                raise serializers.ValidationError(
                    {"triggering_sample": (
                        "A linkage is triggered by a positive result. This "
                        "sample's result is not positive."
                    )}
                )
            if infant is not None and sample.appointment.infant_id != infant.id:
                raise serializers.ValidationError(
                    {"triggering_sample": "This sample belongs to a different infant."}
                )

        if linkage_status == ArtLinkage.Status.STARTED:
            if art_start_date is None:
                raise serializers.ValidationError(
                    {"art_start_date": (
                        "Record the date treatment started. Without it the "
                        "infant drops out of the primary outcome indicator."
                    )}
                )
            issued = sample.result_issued_on if sample else None
            if issued and art_start_date < issued:
                raise serializers.ValidationError(
                    {"art_start_date": (
                        "Treatment cannot start before the laboratory issued "
                        "the result."
                    )}
                )
            if art_start_date > timezone.localdate():
                raise serializers.ValidationError(
                    {"art_start_date": "A start date cannot be in the future."}
                )

        if (
            linkage_status
            in {ArtLinkage.Status.REFUSED, ArtLinkage.Status.UNREACHABLE}
            and not barrier_note.strip()
        ):
            raise serializers.ValidationError(
                {"barrier_note": (
                    "Moving off PENDING silences the linkage alarm, so the "
                    "reason must be on record for the monthly review."
                )}
            )
        return attrs
