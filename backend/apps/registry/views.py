"""
Registry endpoints.

Geography and facilities are read only here: the pilot geography is fixture
data and facility onboarding is a programme-management act done in the admin.
Mentor mothers, clients and infants are written by the supervisor tier and
read by every role a scope allows, which is the split responsibility rule
expressed once in CanEnterClinicalData.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import CanEnterClinicalData, IsActiveHealthWorker
from apps.common.viewsets import ScopedModelViewSet, ScopedReadOnlyModelViewSet

from .filters import ClientFilter, InfantFilter, MentorMotherFilter
from .models import Client, Facility, Infant, LocalGovernmentArea, MentorMother, State
from .serializers import (
    ClientSerializer,
    FacilitySerializer,
    InfantOutcomeSerializer,
    InfantSerializer,
    LgaSerializer,
    MentorMotherSerializer,
    StateSerializer,
)


class StateViewSet(ScopedReadOnlyModelViewSet):
    queryset = State.objects.all()
    serializer_class = StateSerializer
    filterset_fields = ["is_pilot_site"]


class LgaViewSet(ScopedReadOnlyModelViewSet):
    queryset = LocalGovernmentArea.objects.select_related("state")
    serializer_class = LgaSerializer
    filterset_fields = ["state", "is_pilot_site", "is_reserve_site"]


class FacilityViewSet(ScopedReadOnlyModelViewSet):
    queryset = Facility.objects.select_related("lga__state")
    serializer_class = FacilitySerializer
    filterset_fields = ["lga", "lga__state", "level", "is_pilot_site"]


class MentorMotherViewSet(ScopedModelViewSet):
    queryset = MentorMother.objects.select_related("facility", "user")
    serializer_class = MentorMotherSerializer
    permission_classes = [IsActiveHealthWorker, CanEnterClinicalData]
    filterset_class = MentorMotherFilter
    ordering_fields = ["staff_code", "date_engaged"]


class ClientViewSet(ScopedModelViewSet):
    queryset = Client.objects.select_related("facility", "mentor_mother")
    serializer_class = ClientSerializer
    permission_classes = [IsActiveHealthWorker, CanEnterClinicalData]
    filterset_class = ClientFilter
    ordering_fields = ["client_code", "date_enrolled", "expected_delivery_date"]


class InfantViewSet(ScopedModelViewSet):
    queryset = Infant.objects.select_related("mother", "facility")
    serializer_class = InfantSerializer
    permission_classes = [IsActiveHealthWorker, CanEnterClinicalData]
    filterset_class = InfantFilter
    ordering_fields = ["date_of_birth", "baby_code"]

    def perform_create(self, serializer):
        """
        Registering an infant creates the national testing schedule in the
        same transaction. An infant without appointments is invisible to the
        entire safety net, which is exactly the paper-system failure this
        project exists to remove.
        """
        from apps.eid.models import EidAppointment

        with transaction.atomic():
            infant = serializer.save()
            EidAppointment.build_schedule(infant)

    @extend_schema(request=InfantOutcomeSerializer, responses=InfantSerializer)
    @action(detail=True, methods=["post"])
    def outcome(self, request, pk=None):
        """
        Record an outcome transition with a server-set timestamp.

        A terminal outcome is not reversible through the API: undoing a
        recorded death is an administrative correction that must leave an
        audit trail in the admin, not a re-POST.
        """
        serializer = InfantOutcomeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        infant = self.get_object()

        if infant.outcome == Infant.Outcome.DECEASED:
            return Response(
                {"detail": "A deceased outcome cannot be changed through the API."},
                status=status.HTTP_409_CONFLICT,
            )

        infant.outcome = serializer.validated_data["outcome"]
        infant.outcome_recorded_at = timezone.now()
        infant.save(update_fields=["outcome", "outcome_recorded_at", "updated_at"])
        return Response(
            InfantSerializer(infant, context=self.get_serializer_context()).data
        )
