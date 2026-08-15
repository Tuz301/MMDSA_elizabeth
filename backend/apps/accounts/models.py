"""
User accounts and the five-tier access model.

Two ideas are separated here, because merging them is a common cause of data
leaks in health systems.

A role says what a user may do: read a visit, enter a clinical result, create
another user.

A scope says which rows a user may do it to: one facility, one local government
area, one state, or everything.

A supervisor at facility A and a supervisor at facility B hold the same role and
different scopes. Permission code checks the role. Query code applies the scope.
Both checks must pass.
"""

from __future__ import annotations

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.common.fields import EncryptedCharField


class Role(models.TextChoices):
    """The five tiers of the access matrix, from narrowest to widest."""

    MENTOR_MOTHER = "MENTOR_MOTHER", "Mentor mother"
    FACILITY_SUPERVISOR = "FACILITY_SUPERVISOR", "Facility supervisor"
    LGA_COORDINATOR = "LGA_COORDINATOR", "LGA coordinator"
    STATE_MANAGER = "STATE_MANAGER", "State programme manager"
    SYSTEM_ADMIN = "SYSTEM_ADMIN", "System administrator"


#: Roles permitted to enter or change clinical data. A mentor mother is absent
#: from this set by design. She confirms an action by SMS reply; she does not
#: record a laboratory result.
CLINICAL_DATA_ROLES = frozenset(
    {Role.FACILITY_SUPERVISOR, Role.LGA_COORDINATOR, Role.STATE_MANAGER}
)

#: Roles permitted to read a direct identifier: a name, a telephone number, a
#: household address.
IDENTIFIER_READ_ROLES = frozenset(
    {Role.MENTOR_MOTHER, Role.FACILITY_SUPERVISOR, Role.SYSTEM_ADMIN}
)

#: Roles permitted to administer other user accounts.
USER_ADMIN_ROLES = frozenset({Role.STATE_MANAGER, Role.SYSTEM_ADMIN})


class User(AbstractUser):
    """
    A health worker.

    Authentication happens at AWS Cognito. This row holds the programme role and
    the data scope, and it links to the Cognito subject identifier. No password
    hash is used in the pilot environment.
    """

    role = models.CharField(
        max_length=24, choices=Role.choices, default=Role.FACILITY_SUPERVISOR
    )

    cognito_sub = models.CharField(
        max_length=64,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
        help_text="Cognito subject identifier. Links this row to the identity pool.",
    )

    phone_number = EncryptedCharField(max_length=20, blank=True)

    # Scope. Exactly one field is set, matching the role.
    facility = models.ForeignKey(
        "registry.Facility",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="users",
    )
    lga = models.ForeignKey(
        "registry.LocalGovernmentArea",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="users",
    )
    state = models.ForeignKey(
        "registry.State",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="users",
    )

    must_change_password = models.BooleanField(default=True)
    last_active_at = models.DateTimeField(null=True, blank=True)
    disabled_at = models.DateTimeField(null=True, blank=True)
    disabled_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["username"]
        indexes = [models.Index(fields=["role", "facility"])]

    def __str__(self) -> str:
        return f"{self.username} ({self.get_role_display()})"

    # -- Validation --------------------------------------------------------

    def clean(self) -> None:
        """
        Refuse a role and scope combination that does not make sense.

        A facility supervisor with no facility would see either nothing or
        everything, depending on how a filter is written. Both outcomes are
        wrong, so the row is rejected here.
        """
        super().clean()
        required = {
            Role.MENTOR_MOTHER: "facility",
            Role.FACILITY_SUPERVISOR: "facility",
            Role.LGA_COORDINATOR: "lga",
            Role.STATE_MANAGER: "state",
        }
        expected = required.get(Role(self.role))
        if expected and not getattr(self, f"{expected}_id"):
            raise ValidationError(
                {expected: f"A user with the role {self.get_role_display()} "
                           f"must be assigned a {expected}."}
            )
        if self.role == Role.SYSTEM_ADMIN and any(
            [self.facility_id, self.lga_id, self.state_id]
        ):
            raise ValidationError(
                "A system administrator has no scope. Clear the facility, LGA "
                "and state fields."
            )

    # -- Convenience -------------------------------------------------------

    @property
    def is_active_account(self) -> bool:
        return self.is_active and self.disabled_at is None

    @property
    def may_enter_clinical_data(self) -> bool:
        return Role(self.role) in CLINICAL_DATA_ROLES

    @property
    def may_read_identifiers(self) -> bool:
        return Role(self.role) in IDENTIFIER_READ_ROLES

    @property
    def may_administer_users(self) -> bool:
        return Role(self.role) in USER_ADMIN_ROLES

    def touch(self) -> None:
        self.last_active_at = timezone.now()
        self.save(update_fields=["last_active_at"])

    def disable(self, reason: str) -> None:
        self.is_active = False
        self.disabled_at = timezone.now()
        self.disabled_reason = reason
        self.save(update_fields=["is_active", "disabled_at", "disabled_reason"])
