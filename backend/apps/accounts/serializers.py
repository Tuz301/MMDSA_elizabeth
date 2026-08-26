"""Serializers for the caller's own identity and for user administration."""

from __future__ import annotations

from rest_framework import serializers

from apps.common.serializers import ScopedPrimaryKeyRelatedField
from apps.registry.models import Facility, LocalGovernmentArea, State

from .models import Role, User

#: The tiers of the access matrix, for comparing two roles. A user
#: administrator may create or promote only below their own tier; otherwise a
#: state manager could mint a system administrator bound to a Cognito subject
#: they control, and the scope model would mean nothing.
ROLE_TIER = {
    Role.MENTOR_MOTHER: 0,
    Role.FACILITY_SUPERVISOR: 1,
    Role.LGA_COORDINATOR: 2,
    Role.STATE_MANAGER: 3,
    Role.SYSTEM_ADMIN: 4,
}


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

    def validate_role(self, value):
        """
        A user administrator creates or promotes only below their own tier.

        scope_user_queryset limits what a state manager can see; this limits
        what they can make. Without it, visibility scoping is decoration.
        """
        request = self.context.get("request")
        requester = getattr(request, "user", None)
        if requester is None or not requester.is_authenticated:
            raise serializers.ValidationError("No requester on record.")
        if Role(requester.role) == Role.SYSTEM_ADMIN:
            return value
        if ROLE_TIER[Role(value)] >= ROLE_TIER[Role(requester.role)]:
            raise serializers.ValidationError(
                "You may only assign a role below your own."
            )
        return value

    def validate(self, attrs):
        """
        Run the model's own clean() so the role and scope consistency rule
        (a supervisor must have a facility, an administrator must have no
        scope) holds on the API path, not only in the admin.

        The check runs on a throwaway candidate, never on self.instance: a
        mutated instance would keep its half-applied values in memory when a
        later validation step fails.
        """
        def current(name, default=None):
            if name in attrs:
                return attrs[name]
            return getattr(self.instance, name, default) if self.instance else default

        candidate = User(
            username=current("username", ""),
            role=current("role", Role.FACILITY_SUPERVISOR),
        )
        candidate.facility = current("facility")
        candidate.lga = current("lga")
        candidate.state = current("state")
        candidate.clean()
        return attrs


class DisableUserSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=200)
