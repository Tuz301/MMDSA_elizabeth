"""
Registry serializers.

The consent rule is enforced here, on the write path, because the README
records it as a go-live requirement: no consent, no digital record. A client
row cannot be created without a consent timestamp and a form reference, and an
infant cannot be registered under a mother whose consent is absent. Enforcing
it in the serializer means the admin import path must repeat the check, and
the model help text says so; the API path is the one a facility uses daily.
"""

from __future__ import annotations

from django.utils import timezone
from rest_framework import serializers

from apps.common.serializers import (
    IdentifierGatedSerializerMixin,
    ScopedPrimaryKeyRelatedField,
    ScopedSlugRelatedField,
)

from .models import Client, Facility, Infant, LocalGovernmentArea, MentorMother, State


class StateSerializer(serializers.ModelSerializer):
    class Meta:
        model = State
        fields = ["id", "name", "code", "is_pilot_site"]


class LgaSerializer(serializers.ModelSerializer):
    class Meta:
        model = LocalGovernmentArea
        fields = ["id", "state", "name", "code", "is_pilot_site", "is_reserve_site"]


class FacilitySerializer(serializers.ModelSerializer):
    is_ready_for_activation = serializers.BooleanField(read_only=True)

    class Meta:
        model = Facility
        fields = [
            "id", "lga", "name", "code", "level", "latitude", "longitude",
            "is_pilot_site", "pilot_activation_date",
            "has_pmtct_service", "has_eid_sample_point",
            "has_mobile_network_coverage", "has_signed_mou",
            "is_ready_for_activation",
        ]


class MentorMotherSerializer(IdentifierGatedSerializerMixin, serializers.ModelSerializer):
    identifier_fields = ("full_name", "phone_number")

    facility = ScopedPrimaryKeyRelatedField(queryset=Facility.objects.all())
    user = serializers.SlugRelatedField(slug_field="username", read_only=True)
    is_training_passed = serializers.BooleanField(read_only=True)

    class Meta:
        model = MentorMother
        fields = [
            "id", "facility", "user", "staff_code", "full_name", "phone_number",
            "status", "date_engaged", "date_exited", "has_smartphone",
            "training_completed_at", "training_score_percent",
            "is_training_passed", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "user", "is_training_passed", "created_at", "updated_at"]


class ClientSerializer(IdentifierGatedSerializerMixin, serializers.ModelSerializer):
    # The household point is gated with the identifiers: a coordinate pair
    # locates a household as surely as an address does, and no wider tier
    # needs it because the location verdict is computed on the server.
    identifier_fields = (
        "full_name", "phone_number", "alternate_phone_number",
        "household_address", "hospital_number",
        "household_latitude", "household_longitude",
    )

    facility = ScopedPrimaryKeyRelatedField(queryset=Facility.objects.all())
    mentor_mother = ScopedPrimaryKeyRelatedField(
        queryset=MentorMother.objects.all(), required=False, allow_null=True
    )
    has_valid_consent = serializers.BooleanField(read_only=True)

    class Meta:
        model = Client
        fields = [
            "id", "facility", "mentor_mother", "client_code", "hospital_number",
            "full_name", "phone_number", "alternate_phone_number",
            "household_address", "household_latitude", "household_longitude",
            "year_of_birth", "status", "pregnancy_stage",
            "expected_delivery_date", "art_start_date",
            "date_enrolled", "date_exited",
            "consent_given_at", "consent_form_reference", "sms_contact_permitted",
            "has_valid_consent", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "has_valid_consent", "created_at", "updated_at"]

    def validate_consent_given_at(self, value):
        if value is not None and value > timezone.now():
            raise serializers.ValidationError(
                "The consent timestamp cannot be in the future."
            )
        return value

    def validate(self, attrs):
        # No consent, no digital record. The check runs on creation only:
        # an update that does not touch consent must not be blocked by it,
        # and clearing a recorded consent is refused outright.
        if self.instance is None:
            if not attrs.get("consent_given_at"):
                raise serializers.ValidationError(
                    {"consent_given_at": (
                        "A client record cannot be created before the signed "
                        "consent form is recorded."
                    )}
                )
            if not attrs.get("consent_form_reference"):
                raise serializers.ValidationError(
                    {"consent_form_reference": (
                        "Record the reference of the signed consent form."
                    )}
                )
        elif "consent_given_at" in attrs and attrs["consent_given_at"] is None:
            raise serializers.ValidationError(
                {"consent_given_at": (
                    "A recorded consent cannot be cleared here. Withdrawal of "
                    "consent is an administrative act with its own procedure."
                )}
            )

        mentor_mother = attrs.get(
            "mentor_mother",
            self.instance.mentor_mother if self.instance else None,
        )
        facility = attrs.get(
            "facility", self.instance.facility if self.instance else None
        )
        if mentor_mother is not None and facility is not None:
            if mentor_mother.facility_id != facility.id:
                raise serializers.ValidationError(
                    {"mentor_mother": (
                        "The mentor mother works at a different facility from "
                        "this client."
                    )}
                )
        return attrs


class InfantSerializer(IdentifierGatedSerializerMixin, serializers.ModelSerializer):
    identifier_fields = ("given_name",)

    mother = ScopedSlugRelatedField(
        slug_field="client_code", queryset=Client.objects.all()
    )
    facility = ScopedPrimaryKeyRelatedField(queryset=Facility.objects.all())
    age_in_weeks = serializers.IntegerField(read_only=True)
    is_awaiting_art_linkage = serializers.BooleanField(read_only=True)

    class Meta:
        model = Infant
        fields = [
            "id", "mother", "facility", "baby_code", "given_name",
            "date_of_birth", "sex", "birth_weight_grams",
            "received_nevirapine_prophylaxis", "prophylaxis_start_date",
            "outcome", "outcome_recorded_at",
            "age_in_weeks", "is_awaiting_art_linkage",
            "created_at", "updated_at",
        ]
        # baby_code is server-generated and never client-supplied: the
        # randomness is the point. The outcome moves only through the outcome
        # action, so its timestamp cannot be forged in a PATCH.
        read_only_fields = [
            "id", "baby_code", "outcome", "outcome_recorded_at",
            "age_in_weeks", "is_awaiting_art_linkage", "created_at", "updated_at",
        ]

    def validate_date_of_birth(self, value):
        if value > timezone.localdate():
            raise serializers.ValidationError(
                "A date of birth cannot be in the future."
            )
        return value

    def validate(self, attrs):
        mother = attrs.get("mother", self.instance.mother if self.instance else None)
        facility = attrs.get(
            "facility", self.instance.facility if self.instance else None
        )
        if self.instance is None and mother is not None and not mother.has_valid_consent:
            raise serializers.ValidationError(
                {"mother": (
                    "The mother's consent is not on record, so an infant "
                    "cannot be registered under her."
                )}
            )
        if (
            mother is not None
            and facility is not None
            and mother.facility_id != facility.id
        ):
            raise serializers.ValidationError(
                {"facility": (
                    "The infant's facility must match the mother's. A "
                    "transfer is recorded on the mother's record first."
                )}
            )
        return attrs


class InfantOutcomeSerializer(serializers.Serializer):
    outcome = serializers.ChoiceField(choices=Infant.Outcome.choices)
