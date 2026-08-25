"""
Messaging serializers.

No serializer in this app carries a telephone number, gated or otherwise. The
recipient is identified by recipient_reference — a staff or client code —
which is what the model stores for exactly this purpose. A supervisor who
needs a number reads it on the MentorMother or Client record, where the
identifier gate applies and the audit trail records the access.
"""

from __future__ import annotations

from rest_framework import serializers

from .guards import check_template_placeholders
from .models import InboundMessage, MessageTemplate, OutboundMessage


class DeliveryReportSummarySerializer(serializers.Serializer):
    provider_status = serializers.CharField(read_only=True)
    received_at = serializers.DateTimeField(read_only=True)


class OutboundMessageSerializer(serializers.ModelSerializer):
    delivery_latency_seconds = serializers.FloatField(read_only=True)
    is_awaiting_reply = serializers.BooleanField(read_only=True)

    class Meta:
        model = OutboundMessage
        fields = [
            "id", "template_key", "alert", "facility", "recipient_kind",
            "recipient_reference", "render_arguments", "body_length",
            "segment_count", "status", "guard_violations",
            "provider_message_id", "provider_status_detail", "cost_naira",
            "queued_at", "sent_at", "delivered_at", "attempt_count",
            "last_error", "expects_reply", "reply_received_at",
            "delivery_latency_seconds", "is_awaiting_reply", "created_at",
        ]
        read_only_fields = fields


class OutboundMessageDetailSerializer(OutboundMessageSerializer):
    """The detail view adds the delivery history. The raw provider payload
    stays out of the API; it is audit evidence, readable in the admin."""

    delivery_reports = DeliveryReportSummarySerializer(many=True, read_only=True)

    class Meta(OutboundMessageSerializer.Meta):
        fields = OutboundMessageSerializer.Meta.fields + ["delivery_reports"]
        read_only_fields = fields


class InboundMessageSerializer(serializers.ModelSerializer):
    mentor_mother = serializers.SlugRelatedField(
        slug_field="staff_code", read_only=True
    )

    class Meta:
        model = InboundMessage
        fields = [
            # raw_body is held in clear by design: a reply is a keyword and a
            # code, and no message sent by this system ever invites a person
            # to type an identifier.
            "id", "received_at", "raw_body", "parse_status",
            "parsed_keyword", "parsed_code", "matched_message",
            "matched_alert", "mentor_mother", "action_taken", "created_at",
        ]
        read_only_fields = fields


class MessageTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = MessageTemplate
        fields = [
            "id", "key", "language", "body", "expects_reply",
            "reply_keywords", "is_active", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_body(self, value):
        # DRF does not run model clean(), and the guard must hold on the API
        # path too: a template that would insert a name cannot be stored.
        result = check_template_placeholders(value)
        if not result.passed:
            raise serializers.ValidationError(result.violations)
        return value
