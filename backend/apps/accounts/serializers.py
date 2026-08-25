"""Serializers for the caller's own identity and for user administration."""

from __future__ import annotations

from rest_framework import serializers

from apps.common.serializers import ScopedPrimaryKeyRelatedField
from apps.registry.models import Facility, LocalGovernmentArea, State

from .models import User


class ScopeReferenceSerializer(serializers.Serializer):
    """A minimal reference to a scope row: enough to label the UI, no more."""

    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    code = serializers.CharField(read_only=True)


class MeSerializer(serializers.ModelSerializer):
    """
    The caller's own role and scope.

    The mobile client and the dashboard shape their interface from this
    payload instead of guessing from failed requests.
    """

    role_display = serializers.CharField(source="get_role_display", read_only=True)
    facility = ScopeReferenceSerializer(read_only=True)
    lga = ScopeReferenceSerializer(read_only=True)
    state = ScopeReferenceSerializer(read_only=True)
    may_enter_clinical_data = serializers.BooleanField(read_only=True)
    may_read_identifiers = serializers.BooleanField(read_only=True)
    may_administer_users = serializers.BooleanField(read_only=True)
    mentor_mother_staff_code = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "username", "email", "role", "role_display",
            "facility", "lga", "state",
            "may_enter_clinical_data", "may_read_identifiers",
            "may_administer_users", "mentor_mother_staff_code",
            "must_change_password", "last_active_at",
        ]
        read_only_fields = fields

    def get_mentor_mother_staff_code(self, obj) -> str | None:
        profile = getattr(obj, "mentor_mother_profile", None)
        return profile.staff_code if profile else None


class UserSerializer(serializers.ModelSerializer):
    """
    A user row as seen by a user administrator.

    is_active is read only. Enabling and disabling an account are state
    transitions with a recorded reason, reached through the actions on the
    viewset, not by flipping a boolean in a PATCH.
    """

    role_display = serializers.CharField(source="get_role_display", read_only=True)
    facility = ScopedPrimaryKeyRelatedField(
        queryset=Facility.objects.all(), required=False, allow_null=True
    )
    lga = ScopedPrimaryKeyRelatedField(
        queryset=LocalGovernmentArea.objects.all(), required=False, allow_null=True
    )
    state = ScopedPrimaryKeyRelatedField(
        queryset=State.objects.all(), required=False, allow_null=True
    )

    class Meta:
        model = User
        fields = [
            "id", "username", "email", "first_name", "last_name",
            "role", "role_display", "cognito_sub", "phone_number",
            "facility", "lga", "state", "must_change_password",
            "is_active", "last_active_at", "disabled_at", "disabled_reason",
            "date_joined",
        ]
        read_only_fields = [
            "id", "role_display", "is_active", "last_active_at",
            "disabled_at", "disabled_reason", "date_joined",
        ]

    def validate(self, attrs):
        """
        Run the model's own clean() so the role and scope consistency rule
        (a supervisor must have a facility, an administrator must have no
        scope) holds on the API path, not only in the admin.
        """
        candidate = self.instance if self.instance is not None else User()
        original = {
            name: getattr(candidate, name)
            for name in ("role", "facility", "lga", "state")
        }
        try:
            for name, value in attrs.items():
                if hasattr(candidate, name):
                    setattr(candidate, name, value)
            candidate.clean()
        finally:
            for name, value in original.items():
                setattr(candidate, name, value)
        return attrs


class DisableUserSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=200)
