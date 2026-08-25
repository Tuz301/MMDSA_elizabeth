"""
Alert endpoints.

The list is ordered by an explicit severity rank, not the model's Meta
ordering. Ordering a CharField descending sorts {CRITICAL, HIGH, LOW, MEDIUM}
lexicographically, which puts a critical alert last — the opposite of
clinical urgency. The annotation below is the correct order, and the nearest
deadline breaks ties.
"""

from __future__ import annotations

import django_filters
from django.db.models import Case, IntegerField, When
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import Role
from apps.accounts.permissions import CanAdministerUsers, CanEnterClinicalData, IsActiveHealthWorker
from apps.common.viewsets import ScopedReadOnlyModelViewSet

from .models import Alert, AlertRule, Severity
from .serializers import AlertResolveSerializer, AlertRuleSerializer, AlertSerializer


class AlertFilter(django_filters.FilterSet):
    past_deadline = django_filters.BooleanFilter(method="filter_past_deadline")

    class Meta:
        model = Alert
        fields = {
            "status": ["exact"],
            "severity": ["exact"],
            "alert_type": ["exact"],
            "facility": ["exact"],
            "assigned_mentor_mother": ["exact"],
            "raised_at": ["gte", "lte"],
        }

    def filter_past_deadline(self, queryset, name, value):
        if value is True:
            return queryset.filter(
                status__in=[Alert.Status.OPEN, Alert.Status.ESCALATED],
                acknowledge_by__lt=timezone.now(),
            )
        return queryset


SEVERITY_RANK = Case(
    When(severity=Severity.CRITICAL, then=0),
    When(severity=Severity.HIGH, then=1),
    When(severity=Severity.MEDIUM, then=2),
    When(severity=Severity.LOW, then=3),
    default=4,
    output_field=IntegerField(),
)


class AlertViewSet(ScopedReadOnlyModelViewSet):
    queryset = Alert.objects.select_related(
        "facility", "assigned_user", "assigned_mentor_mother", "acknowledged_by"
    ).prefetch_related("escalations__escalated_to")
    serializer_class = AlertSerializer
    filterset_class = AlertFilter
    ordering_fields = ["raised_at", "acknowledge_by"]

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .annotate(severity_rank=SEVERITY_RANK)
            .order_by("severity_rank", "acknowledge_by")
        )

    def get_permissions(self):
        # A mentor mother acknowledges an alert assigned to her; scoping
        # already narrows her to those rows, so an out-of-scope id is a 404.
        # Resolution asserts the underlying clinical condition is handled,
        # which is supervisor work.
        if self.action == "resolve":
            return [IsActiveHealthWorker(), CanEnterClinicalData()]
        return [IsActiveHealthWorker()]

    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        alert = self.get_object()
        alert.acknowledge(user=request.user, channel="APP")
        # The model method no-ops on RESOLVED and CANCELLED; returning the row
        # either way lets a stale client converge on the true state.
        return Response(self.get_serializer(alert).data)

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        serializer = AlertResolveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        alert = self.get_object()
        if alert.status == Alert.Status.OPEN:
            return Response(
                {"detail": (
                    "Acknowledge the alert before resolving it. Resolution "
                    "without acknowledgement would hide the relay delay the "
                    "pilot measures."
                )},
                status=status.HTTP_409_CONFLICT,
            )
        if alert.status == Alert.Status.CANCELLED:
            return Response(
                {"detail": "A cancelled alert cannot be resolved."},
                status=status.HTTP_409_CONFLICT,
            )
        alert.resolve(note=serializer.validated_data["note"])
        return Response(self.get_serializer(alert).data)

    @action(detail=False, methods=["get"])
    def mine(self, request):
        """The caller's own open work: assigned alerts for a mentor mother,
        the facility's open alerts for everyone else."""
        queryset = self.filter_queryset(self.get_queryset()).filter(
            status__in=[Alert.Status.OPEN, Alert.Status.ESCALATED]
        )
        if Role(request.user.role) == Role.MENTOR_MOTHER:
            profile = getattr(request.user, "mentor_mother_profile", None)
            queryset = queryset.filter(
                assigned_mentor_mother=profile.id if profile else None
            )
        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)


class AlertRuleViewSet(viewsets.ModelViewSet):
    """Thresholds are data, not code. Every tier reads the rules to predict
    what the engine will do; changing one is a programme-manager act."""

    queryset = AlertRule.objects.all()
    serializer_class = AlertRuleSerializer
    http_method_names = ["get", "patch", "head", "options"]

    def get_permissions(self):
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            return [IsActiveHealthWorker()]
        return [IsActiveHealthWorker(), CanAdministerUsers()]
