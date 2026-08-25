"""
EID endpoints: the measurement the pilot exists to change.

Result entry and acknowledgement are separate actions on purpose, even though
the same supervisor will usually perform both minutes apart. The gap between
the two timestamps is the relay delay, and collapsing them into one write
would zero the primary indicator by construction. The alert engine raises
POS_UNACK from that gap; the API never raises alerts itself.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.db.models import Case, IntegerField, When
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import CanEnterClinicalData, IsActiveHealthWorker
from apps.common.viewsets import ScopedModelViewSet
from apps.registry.models import Infant

from .filters import ArtLinkageFilter, EidAppointmentFilter, EidSampleFilter
from .models import ArtLinkage, EidAppointment, EidSample
from .serializers import (
    AppointmentAttendedSerializer,
    AppointmentRescheduleSerializer,
    ArtLinkageSerializer,
    EidAppointmentSerializer,
    EidSampleSerializer,
    SampleResultSerializer,
)


class EidAppointmentViewSet(ScopedModelViewSet):
    """Appointments. Status moves through actions; there is no PATCH at all,
    because every field on this model is either identity or a transition."""

    queryset = EidAppointment.objects.select_related("infant__facility")
    serializer_class = EidAppointmentSerializer
    permission_classes = [IsActiveHealthWorker, CanEnterClinicalData]
    http_method_names = ["get", "post", "head", "options"]
    filterset_class = EidAppointmentFilter
    ordering_fields = ["due_date"]

    def _transition(self, appointment, allowed_from, message):
        if appointment.status not in allowed_from:
            return Response(
                {"detail": message.format(status=appointment.get_status_display())},
                status=status.HTTP_409_CONFLICT,
            )
        return None

    @action(detail=True, methods=["post"])
    def attended(self, request, pk=None):
        serializer = AppointmentAttendedSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        appointment = self.get_object()
        refusal = self._transition(
            appointment,
            {EidAppointment.Status.SCHEDULED, EidAppointment.Status.RESCHEDULED},
            "This appointment is {status} and cannot be marked attended.",
        )
        if refusal:
            return refusal
        attended_date = serializer.validated_data["attended_date"]
        if attended_date < appointment.infant.date_of_birth:
            return Response(
                {"detail": "The attendance date is before the infant's date of birth."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        appointment.status = EidAppointment.Status.ATTENDED
        appointment.attended_date = attended_date
        appointment.save(update_fields=["status", "attended_date", "updated_at"])
        return Response(self.get_serializer(appointment).data)

    @action(detail=True, methods=["post"])
    def missed(self, request, pk=None):
        appointment = self.get_object()
        refusal = self._transition(
            appointment,
            {EidAppointment.Status.SCHEDULED, EidAppointment.Status.RESCHEDULED},
            "This appointment is {status} and cannot be marked missed.",
        )
        if refusal:
            return refusal
        appointment.status = EidAppointment.Status.MISSED
        appointment.save(update_fields=["status", "updated_at"])
        return Response(self.get_serializer(appointment).data)

    @action(detail=True, methods=["post"])
    def reschedule(self, request, pk=None):
        serializer = AppointmentRescheduleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        appointment = self.get_object()
        refusal = self._transition(
            appointment,
            {
                EidAppointment.Status.SCHEDULED,
                EidAppointment.Status.RESCHEDULED,
                EidAppointment.Status.MISSED,
            },
            "This appointment is {status} and cannot be rescheduled.",
        )
        if refusal:
            return refusal
        appointment.status = EidAppointment.Status.RESCHEDULED
        appointment.due_date = serializer.validated_data["due_date"]
        appointment.save(update_fields=["status", "due_date", "updated_at"])
        return Response(self.get_serializer(appointment).data)


class EidSampleViewSet(ScopedModelViewSet):
    """Samples. The default ordering puts the oldest unacknowledged positive
    at the very top of the list, because that row is the reason the system
    exists and the model's own -collected_on ordering would bury it."""

    queryset = EidSample.objects.select_related(
        "appointment__infant__facility", "entered_by", "acknowledged_by"
    )
    serializer_class = EidSampleSerializer
    permission_classes = [IsActiveHealthWorker, CanEnterClinicalData]
    filterset_class = EidSampleFilter
    ordering_fields = ["collected_on", "result_entered_at"]

    def get_queryset(self):
        unacknowledged_positive_first = Case(
            When(
                result=EidSample.Result.POSITIVE,
                result_acknowledged_at__isnull=True,
                then=0,
            ),
            default=1,
            output_field=IntegerField(),
        )
        return (
            super()
            .get_queryset()
            .annotate(triage_rank=unacknowledged_positive_first)
            .order_by("triage_rank", "result_entered_at", "-collected_on")
        )

    def perform_create(self, serializer):
        """Registering a sample marks its appointment attended in the same
        transaction: ATTENDED is defined as attended-with-sample."""
        with transaction.atomic():
            sample = serializer.save()
            appointment = sample.appointment
            if appointment.status != EidAppointment.Status.ATTENDED:
                appointment.status = EidAppointment.Status.ATTENDED
                appointment.attended_date = sample.collected_on
                appointment.save(
                    update_fields=["status", "attended_date", "updated_at"]
                )

    @action(detail=True, methods=["post"])
    def result(self, request, pk=None):
        """
        Enter the laboratory result.

        Sets entry fields only, never acknowledgement. A positive result also
        flips the infant's outcome and opens the PENDING treatment linkage in
        the same transaction, so both alarm clocks start from this moment: an
        unacknowledged positive raises POS_UNACK, an unlinked infant raises
        LINK_OVERDUE. Creating the linkage later, or never, would leave the
        weekly linkage alarm with nothing to scan.
        """
        serializer = SampleResultSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sample = self.get_object()

        if sample.result != EidSample.Result.PENDING:
            return Response(
                {"detail": (
                    "A result is already entered. A correction is an "
                    "administrative act with its own audit trail, not a re-POST."
                )},
                status=status.HTTP_409_CONFLICT,
            )
        issued_on = serializer.validated_data["result_issued_on"]
        if issued_on < sample.collected_on:
            return Response(
                {"detail": "A result cannot be issued before the sample was collected."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            sample.result = serializer.validated_data["result"]
            sample.result_issued_on = issued_on
            if serializer.validated_data.get("laboratory_name"):
                sample.laboratory_name = serializer.validated_data["laboratory_name"]
            sample.result_entered_at = timezone.now()
            sample.entered_by = request.user
            sample.save(
                update_fields=[
                    "result", "result_issued_on", "laboratory_name",
                    "result_entered_at", "entered_by", "updated_at",
                ]
            )

            infant = sample.appointment.infant
            if sample.result == EidSample.Result.POSITIVE:
                infant.outcome = Infant.Outcome.HIV_POSITIVE_NOT_LINKED
                infant.outcome_recorded_at = timezone.now()
                infant.save(
                    update_fields=["outcome", "outcome_recorded_at", "updated_at"]
                )
                ArtLinkage.objects.get_or_create(
                    infant=infant,
                    defaults={
                        "triggering_sample": sample,
                        "status": ArtLinkage.Status.PENDING,
                        "recorded_by": request.user,
                    },
                )
            elif sample.result == EidSample.Result.INDETERMINATE:
                # One repeat slot exists per infant by schema. A second
                # indeterminate result cannot get a second repeat appointment
                # without a schema change; the get_or_create keeps this
                # request from failing when that limit is reached.
                EidAppointment.objects.get_or_create(
                    infant=infant,
                    milestone=EidAppointment.Milestone.UNSCHEDULED,
                    defaults={"due_date": timezone.localdate() + timedelta(days=14)},
                )

        return Response(self.get_serializer(sample).data)

    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        """
        Acknowledge an entered result.

        Write-once: a second acknowledgement returns the existing state
        unchanged, because overwriting the timestamp would rewrite the relay
        delay measurement. Acknowledging resolves the sample's open POS_UNACK
        alert in the same transaction, so the alert record and the sample
        record cannot contradict each other.
        """
        sample = self.get_object()
        if sample.result_entered_at is None or sample.result == EidSample.Result.PENDING:
            return Response(
                {"detail": "No result has been entered yet, so there is nothing to acknowledge."},
                status=status.HTTP_409_CONFLICT,
            )
        if sample.result_acknowledged_at is not None:
            return Response(self.get_serializer(sample).data)

        from apps.alerts.models import Alert

        with transaction.atomic():
            sample.result_acknowledged_at = timezone.now()
            sample.acknowledged_by = request.user
            sample.save(
                update_fields=["result_acknowledged_at", "acknowledged_by", "updated_at"]
            )
            open_alerts = Alert.objects.filter(
                alert_type="POS_UNACK",
                subject_type="eid.EidSample",
                subject_id=sample.pk,
                status__in=[Alert.Status.OPEN, Alert.Status.ESCALATED],
            )
            for alert in open_alerts:
                alert.acknowledge(user=request.user, channel="APP")
                alert.resolve(note="The result was acknowledged in the application.")

        return Response(self.get_serializer(sample).data)

    @action(detail=True, methods=["post"], url_path="caregiver-informed")
    def caregiver_informed(self, request, pk=None):
        sample = self.get_object()
        if sample.result_acknowledged_at is None:
            return Response(
                {"detail": "Acknowledge the result before recording that the caregiver was informed."},
                status=status.HTTP_409_CONFLICT,
            )
        if sample.caregiver_informed_at is not None:
            return Response(self.get_serializer(sample).data)
        sample.caregiver_informed_at = timezone.now()
        sample.save(update_fields=["caregiver_informed_at", "updated_at"])
        return Response(self.get_serializer(sample).data)


class ArtLinkageViewSet(ScopedModelViewSet):
    """Treatment linkage. Status here is user-entered clinical fact, so it
    stays writable; the outcome flips on the infant happen server-side in the
    same transaction as the status change."""

    queryset = ArtLinkage.objects.select_related(
        "infant__facility", "triggering_sample", "treating_facility", "recorded_by"
    )
    serializer_class = ArtLinkageSerializer
    permission_classes = [IsActiveHealthWorker, CanEnterClinicalData]
    filterset_class = ArtLinkageFilter
    ordering_fields = ["created_at", "art_start_date"]

    #: Outcome the infant takes when the linkage reaches each status. REFUSED
    #: and UNREACHABLE are absent on purpose: the infant is still awaiting
    #: linkage, so the outcome stays POS_NOT_LINKED and the weekly linkage
    #: alarm keeps firing.
    _OUTCOME_BY_STATUS = {
        ArtLinkage.Status.STARTED: Infant.Outcome.HIV_POSITIVE_ON_ART,
        ArtLinkage.Status.DECEASED: Infant.Outcome.DECEASED,
        ArtLinkage.Status.TRANSFERRED: Infant.Outcome.TRANSFERRED,
    }

    def _save_with_outcome(self, serializer):
        with transaction.atomic():
            linkage = serializer.save(recorded_by=self.request.user)
            outcome = self._OUTCOME_BY_STATUS.get(linkage.status)
            if outcome is not None and linkage.infant.outcome != outcome:
                linkage.infant.outcome = outcome
                linkage.infant.outcome_recorded_at = timezone.now()
                linkage.infant.save(
                    update_fields=["outcome", "outcome_recorded_at", "updated_at"]
                )

    def perform_create(self, serializer):
        self._save_with_outcome(serializer)

    def perform_update(self, serializer):
        self._save_with_outcome(serializer)
