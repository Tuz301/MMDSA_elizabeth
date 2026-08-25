"""Messaging read endpoints and template administration. Messages are created
only by the engine and the gateway; the API observes the delivery evidence."""

from __future__ import annotations

import django_filters
from rest_framework import viewsets

from apps.accounts.permissions import CanAdministerUsers, IsActiveHealthWorker
from apps.common.viewsets import ScopedReadOnlyModelViewSet

from .models import InboundMessage, MessageTemplate, OutboundMessage
from .serializers import (
    InboundMessageSerializer,
    MessageTemplateSerializer,
    OutboundMessageDetailSerializer,
    OutboundMessageSerializer,
)


class OutboundMessageFilter(django_filters.FilterSet):
    awaiting_reply = django_filters.BooleanFilter(method="filter_awaiting_reply")

    class Meta:
        model = OutboundMessage
        fields = {
            "status": ["exact"],
            "facility": ["exact"],
            "recipient_kind": ["exact"],
            "template_key": ["exact"],
            "expects_reply": ["exact"],
            "alert": ["exact"],
            "queued_at": ["gte", "lte"],
        }

    def filter_awaiting_reply(self, queryset, name, value):
        if value is True:
            return queryset.filter(
                expects_reply=True, reply_received_at__isnull=True
            )
        return queryset


class OutboundMessageViewSet(ScopedReadOnlyModelViewSet):
    queryset = OutboundMessage.objects.select_related("facility", "alert")
    serializer_class = OutboundMessageSerializer
    filterset_class = OutboundMessageFilter
    ordering_fields = ["queued_at", "sent_at"]

    def get_serializer_class(self):
        if self.action == "retrieve":
            return OutboundMessageDetailSerializer
        return OutboundMessageSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == "retrieve":
            queryset = queryset.prefetch_related("delivery_reports")
        return queryset


class InboundMessageViewSet(ScopedReadOnlyModelViewSet):
    queryset = InboundMessage.objects.select_related(
        "mentor_mother", "matched_message", "matched_alert"
    )
    serializer_class = InboundMessageSerializer
    filterset_fields = ["parse_status", "mentor_mother"]
    ordering_fields = ["received_at"]


class MessageTemplateViewSet(viewsets.ModelViewSet):
    """Templates are configuration: readable by every active worker so anyone
    can predict what a message will say, changed only by the admin tier, and
    retired with is_active rather than deleted, because sent messages still
    reference them."""

    queryset = MessageTemplate.objects.all()
    serializer_class = MessageTemplateSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]
    filterset_fields = ["key", "language", "is_active"]

    def get_permissions(self):
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            return [IsActiveHealthWorker()]
        return [IsActiveHealthWorker(), CanAdministerUsers()]
