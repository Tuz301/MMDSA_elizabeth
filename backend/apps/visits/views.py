"""
The supervisor's review loop over visits.

A flagged visit is a prompt for a conversation, never a finding of
misconduct, and the endpoints keep that shape: review records what the
supervisor found, and the verdict fields the server computed are not
writable by anybody.
"""

from __future__ import annotations

from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import CanEnterClinicalData, IsActiveHealthWorker
from apps.common.viewsets import ScopedReadOnlyModelViewSet

from .models import GeospatialAnomaly, HomeVisit, SyncBatch
from .serializers import (
    AnomalyDispositionSerializer,
    GeospatialAnomalySerializer,
    HomeVisitSerializer,
    SyncBatchSerializer,
    VisitReviewSerializer,
)


class HomeVisitViewSet(ScopedReadOnlyModelViewSet):
    queryset = HomeVisit.objects.select_related(
        "client", "infant", "mentor_mother", "reviewed_by"
    )
    serializer_class = HomeVisitSerializer
    filterset_fields = [
        "client", "mentor_mother", "client__facility", "purpose", "result",
        "location_status", "flagged_for_review",
    ]
    ordering_fields = ["visit_date"]

    @extend_schema(request=VisitReviewSerializer, responses=HomeVisitSerializer)
    @action(
        detail=True,
        methods=["post"],
        permission_classes=[IsActiveHealthWorker, CanEnterClinicalData],
    )
    def review(self, request, pk=None):
        serializer = VisitReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        visit = self.get_object()
        if visit.reviewed_at is not None:
            return Response(
                {"detail": "This visit has already been reviewed."},
                status=status.HTTP_409_CONFLICT,
            )
        visit.reviewed_by = request.user
        visit.reviewed_at = timezone.now()
        visit.review_outcome = serializer.validated_data["review_outcome"]
        visit.save(
            update_fields=["reviewed_by", "reviewed_at", "review_outcome", "updated_at"]
        )
        return Response(self.get_serializer(visit).data)


class GeospatialAnomalyViewSet(ScopedReadOnlyModelViewSet):
    queryset = GeospatialAnomaly.objects.select_related(
        "mentor_mother", "reviewed_by"
    ).prefetch_related("visits")
    serializer_class = GeospatialAnomalySerializer
    filterset_fields = ["mentor_mother", "kind", "disposition", "confidence"]
    ordering_fields = ["detected_for_date"]

    @extend_schema(request=AnomalyDispositionSerializer, responses=GeospatialAnomalySerializer)
    @action(
        detail=True,
        methods=["post"],
        permission_classes=[IsActiveHealthWorker, CanEnterClinicalData],
    )
    def disposition(self, request, pk=None):
        serializer = AnomalyDispositionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        anomaly = self.get_object()
        if anomaly.disposition != GeospatialAnomaly.Disposition.OPEN:
            return Response(
                {"detail": "This anomaly has already been reviewed."},
                status=status.HTTP_409_CONFLICT,
            )
        anomaly.disposition = serializer.validated_data["disposition"]
        anomaly.review_note = serializer.validated_data["review_note"]
        anomaly.reviewed_by = request.user
        anomaly.reviewed_at = timezone.now()
        anomaly.save(
            update_fields=[
                "disposition", "review_note", "reviewed_by", "reviewed_at", "updated_at",
            ]
        )
        return Response(self.get_serializer(anomaly).data)


class SyncBatchViewSet(ScopedReadOnlyModelViewSet):
    """How a supervisor sees a handset that is holding data. A handset that
    has not synchronised is holding visit records nobody can act on."""

    queryset = SyncBatch.objects.select_related("user")
    serializer_class = SyncBatchSerializer
    filterset_fields = ["device_id", "user", "status"]
    ordering_fields = ["created_at"]
