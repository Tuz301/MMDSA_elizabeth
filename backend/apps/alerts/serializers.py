"""Alert serializers. Everything on an alert is read only through the API:
only the engine raises alerts, and acknowledgement and resolution are the
outcome measurement, reachable only through their actions."""

from __future__ import annotations

from rest_framework import serializers

from .models import Alert, AlertEscalation, AlertRule


class AlertEscalationSerializer(serializers.ModelSerializer):
    escalated_to = serializers.SlugRelatedField(slug_field="username", read_only=True)

    class Meta:
        model = AlertEscalation
        fields = ["level", "escalated_to", "reason", "created_at"]


class AlertSerializer(serializers.ModelSerializer):
    assigned_user = serializers.SlugRelatedField(slug_field="username", read_only=True)
    assigned_mentor_mother = serializers.SlugRelatedField(
        slug_field="staff_code", read_only=True
    )
    acknowledged_by = serializers.SlugRelatedField(
        slug_field="username", read_only=True
    )
    is_past_deadline = serializers.BooleanField(read_only=True)
    hours_to_acknowledgement = serializers.FloatField(read_only=True)
    escalations = AlertEscalationSerializer(many=True, read_only=True)

    class Meta:
        model = Alert
        fields = [
            "id", "alert_type", "severity", "status", "facility",
            "assigned_user", "assigned_mentor_mother",
            "subject_type", "subject_id", "title", "detail",
            "raised_at", "acknowledge_by", "acknowledged_at", "acknowledged_by",
            "acknowledgement_channel", "resolved_at", "resolution_note",
            "escalation_level", "escalated_at",
            "is_past_deadline", "hours_to_acknowledgement",
            "escalations", "created_at",
        ]
        read_only_fields = fields


class AlertResolveSerializer(serializers.Serializer):
    """Resolution asserts the underlying condition is dealt with, so the note
    is required: an empty resolution is indistinguishable from a dismissal."""

    note = serializers.CharField(max_length=2000)


class AlertRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = AlertRule
        fields = [
            "id", "alert_type", "is_enabled", "severity",
            "threshold_value", "threshold_unit",
            "notify_mentor_mother", "notify_facility_supervisor",
            "notify_lga_coordinator", "send_sms", "sms_template_key",
            "escalate_after_hours", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "alert_type", "created_at", "updated_at"]
