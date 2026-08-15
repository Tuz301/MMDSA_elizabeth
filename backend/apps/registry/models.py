"""
Programme registry: geography, facilities, mentor mothers, clients and infants.

Two design rules run through this module.

First, a direct identifier is separated from a programme identifier. Every
client and every infant carries a code that is meaningless outside the
database. Reports, dashboards, exports and SMS messages use the code. Names,
telephone numbers and addresses are encrypted and are read only by a user whose
role permits it.

Second, the pilot geography is data, not code. The states and local government
areas are loaded from a fixture. No state name is written into a model, a
migration or a constant, so a change of pilot site needs no code change.
"""

from __future__ import annotations

import secrets
from datetime import date

from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone

from apps.common.fields import EncryptedCharField, EncryptedTextField
from apps.common.models import BaseModel

PHONE_VALIDATOR = RegexValidator(
    regex=r"^\+234[789][01]\d{8}$",
    message="Enter a Nigerian mobile number in the form +2348012345678.",
)


class State(BaseModel):
    """A Nigerian state or the Federal Capital Territory."""

    name = models.CharField(max_length=60, unique=True)
    code = models.CharField(max_length=8, unique=True)
    is_pilot_site = models.BooleanField(
        default=False,
        help_text="True for a state included in the current pilot.",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class LocalGovernmentArea(BaseModel):
    """A local government area. The unit at which a coordinator supervises."""

    state = models.ForeignKey(State, on_delete=models.PROTECT, related_name="lgas")
    name = models.CharField(max_length=80)
    code = models.CharField(max_length=12, unique=True)
    is_pilot_site = models.BooleanField(default=False)
    is_reserve_site = models.BooleanField(
        default=False,
        help_text=(
            "True for an area held in reserve. A reserve area replaces a "
            "primary area that withdraws or fails the readiness check."
        ),
    )

    class Meta:
        ordering = ["state__name", "name"]
        unique_together = [("state", "name")]

    def __str__(self) -> str:
        return f"{self.name}, {self.state.name}"


class Facility(BaseModel):
    """A health facility that delivers PMTCT services."""

    class Level(models.TextChoices):
        PRIMARY = "PRIMARY", "Primary health centre"
        SECONDARY = "SECONDARY", "Secondary, general hospital"
        TERTIARY = "TERTIARY", "Tertiary, teaching hospital"

    lga = models.ForeignKey(
        LocalGovernmentArea, on_delete=models.PROTECT, related_name="facilities"
    )
    name = models.CharField(max_length=160)
    code = models.CharField(
        max_length=20, unique=True, help_text="National health facility code."
    )
    level = models.CharField(max_length=12, choices=Level.choices)

    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )

    is_pilot_site = models.BooleanField(default=False)
    pilot_activation_date = models.DateField(null=True, blank=True)

    # Readiness criteria from the implementation plan. All four must hold
    # before a facility is activated.
    has_pmtct_service = models.BooleanField(default=True)
    has_eid_sample_point = models.BooleanField(default=False)
    has_mobile_network_coverage = models.BooleanField(default=True)
    has_signed_mou = models.BooleanField(default=False)

    class Meta:
        ordering = ["lga__state__name", "lga__name", "name"]
        verbose_name_plural = "facilities"

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"

    @property
    def is_ready_for_activation(self) -> bool:
        return all(
            [
                self.has_pmtct_service,
                self.has_eid_sample_point,
                self.has_mobile_network_coverage,
                self.has_signed_mou,
            ]
        )


class MentorMother(BaseModel):
    """
    A mentor mother: a woman living with HIV who supports other women in the
    PMTCT programme.

    A mentor mother logs home visits and receives alerts. She does not enter
    clinical data. That split is deliberate and is enforced by the permission
    layer, not only by the user interface.
    """

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        ON_LEAVE = "ON_LEAVE", "On leave"
        EXITED = "EXITED", "Exited the programme"

    facility = models.ForeignKey(
        Facility, on_delete=models.PROTECT, related_name="mentor_mothers"
    )
    user = models.OneToOneField(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mentor_mother_profile",
        help_text="Login account. Empty for a mentor mother who works by SMS only.",
    )

    staff_code = models.CharField(
        max_length=20,
        unique=True,
        help_text="Programme identifier. Used in every report and every SMS.",
    )
    full_name = EncryptedCharField(max_length=120)
    phone_number = EncryptedCharField(
        max_length=20, validators=[PHONE_VALIDATOR], help_text="Encrypted at rest."
    )

    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.ACTIVE
    )
    date_engaged = models.DateField(default=date.today)
    date_exited = models.DateField(null=True, blank=True)

    has_smartphone = models.BooleanField(
        default=True,
        help_text=(
            "False means this mentor mother works by SMS only. The alert "
            "engine sends her a structured message and parses her reply."
        ),
    )
    training_completed_at = models.DateField(null=True, blank=True)
    training_score_percent = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["staff_code"]

    def __str__(self) -> str:
        return self.staff_code

    @property
    def is_training_passed(self) -> bool:
        """The training curriculum sets a pass mark of 80 percent."""
        return (self.training_score_percent or 0) >= 80


class Client(BaseModel):
    """
    A woman enrolled in the PMTCT programme.

    client_code is the only identifier that leaves the database. Names,
    telephone numbers and addresses are encrypted.
    """

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active in care"
        TRANSFERRED = "TRANSFERRED", "Transferred out"
        LOST_TO_FOLLOW_UP = "LTFU", "Lost to follow up"
        EXITED = "EXITED", "Exited, programme complete"
        DECEASED = "DECEASED", "Deceased"

    class PregnancyStage(models.TextChoices):
        ANTENATAL = "ANC", "Antenatal"
        LABOUR = "LABOUR", "In labour or delivery"
        POSTNATAL = "PNC", "Postnatal"
        NOT_PREGNANT = "NONE", "Not currently pregnant"

    facility = models.ForeignKey(
        Facility, on_delete=models.PROTECT, related_name="clients"
    )
    mentor_mother = models.ForeignKey(
        MentorMother,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="clients",
    )

    client_code = models.CharField(
        max_length=24,
        unique=True,
        help_text="Programme identifier. Safe to use in reports and dashboards.",
    )
    hospital_number = EncryptedCharField(max_length=40, blank=True)

    full_name = EncryptedCharField(max_length=120)
    phone_number = EncryptedCharField(
        max_length=20, blank=True, validators=[PHONE_VALIDATOR]
    )
    alternate_phone_number = EncryptedCharField(max_length=20, blank=True)
    household_address = EncryptedTextField(blank=True)

    # The household point is held in clear because a coordinate on its own does
    # not name a person, and the visit verification calculation needs to run in
    # the database. Access is still restricted by role.
    household_latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    household_longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )

    year_of_birth = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1930)],
        help_text=(
            "Year only. A full date of birth adds re-identification risk and "
            "the programme does not need it."
        ),
    )

    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    pregnancy_stage = models.CharField(
        max_length=8, choices=PregnancyStage.choices, default=PregnancyStage.ANTENATAL
    )
    expected_delivery_date = models.DateField(null=True, blank=True)

    art_start_date = models.DateField(null=True, blank=True)
    date_enrolled = models.DateField(default=date.today)
    date_exited = models.DateField(null=True, blank=True)

    consent_given_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Time the signed consent form was recorded. No consent, no digital record.",
    )
    consent_form_reference = models.CharField(max_length=60, blank=True)
    sms_contact_permitted = models.BooleanField(
        default=False,
        help_text=(
            "The client agreed to receive SMS. A client may join the "
            "programme and refuse SMS contact."
        ),
    )

    class Meta:
        ordering = ["client_code"]
        indexes = [
            models.Index(fields=["facility", "status"]),
            models.Index(fields=["mentor_mother", "status"]),
        ]

    def __str__(self) -> str:
        return self.client_code

    @property
    def has_valid_consent(self) -> bool:
        return self.consent_given_at is not None


def generate_baby_code() -> str:
    """
    Return a Baby Code: a short, random, non-sequential identifier.

    The code is random rather than sequential on purpose. A sequential code
    would show how many infants are enrolled at a site and would let a person
    who sees one code guess a neighbouring one.
    """
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # No I, O, 0 or 1.
    return "B" + "".join(secrets.choice(alphabet) for _ in range(7))


class Infant(BaseModel):
    """
    An HIV exposed infant.

    THE BABY CODE RULE. Any message that leaves the system identifies an infant
    by baby_code and by nothing else. No name, no mother's name, no facility
    address, no HIV status word. An SMS crosses a public network and is stored
    in plain text on the handset. A message that names a child and implies an
    HIV exposure puts that family at risk of harm.

    The rule is enforced in apps.messaging.guards, which inspects every outbound
    message body. It is not left to the discretion of the person writing a
    template.
    """

    class Sex(models.TextChoices):
        FEMALE = "F", "Female"
        MALE = "M", "Male"

    class Outcome(models.TextChoices):
        IN_FOLLOW_UP = "IN_FOLLOW_UP", "In follow up"
        HIV_NEGATIVE_DISCHARGED = "NEG_DISCHARGED", "HIV negative, discharged"
        HIV_POSITIVE_ON_ART = "POS_ON_ART", "HIV positive, on treatment"
        HIV_POSITIVE_NOT_LINKED = "POS_NOT_LINKED", "HIV positive, not yet on treatment"
        LOST_TO_FOLLOW_UP = "LTFU", "Lost to follow up"
        TRANSFERRED = "TRANSFERRED", "Transferred out"
        DECEASED = "DECEASED", "Deceased"

    mother = models.ForeignKey(
        Client, on_delete=models.PROTECT, related_name="infants"
    )
    facility = models.ForeignKey(
        Facility, on_delete=models.PROTECT, related_name="infants"
    )

    baby_code = models.CharField(
        max_length=12,
        unique=True,
        default=generate_baby_code,
        help_text=(
            "The only identifier permitted in an SMS. Random, not sequential."
        ),
    )
    given_name = EncryptedCharField(max_length=80, blank=True)

    date_of_birth = models.DateField(db_index=True)
    sex = models.CharField(max_length=1, choices=Sex.choices)
    birth_weight_grams = models.PositiveIntegerField(null=True, blank=True)

    received_nevirapine_prophylaxis = models.BooleanField(null=True, blank=True)
    prophylaxis_start_date = models.DateField(null=True, blank=True)

    outcome = models.CharField(
        max_length=16,
        choices=Outcome.choices,
        default=Outcome.IN_FOLLOW_UP,
        db_index=True,
    )
    outcome_recorded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date_of_birth"]
        indexes = [
            models.Index(fields=["facility", "outcome"]),
            models.Index(fields=["date_of_birth", "outcome"]),
        ]

    def __str__(self) -> str:
        return self.baby_code

    @property
    def age_in_weeks(self) -> int:
        return (timezone.localdate() - self.date_of_birth).days // 7

    @property
    def is_awaiting_art_linkage(self) -> bool:
        """
        True for an infant who has tested positive and is not yet on treatment.

        This is the gap the pilot exists to close. Every such infant should
        carry an open alert.
        """
        return self.outcome == self.Outcome.HIV_POSITIVE_NOT_LINKED
