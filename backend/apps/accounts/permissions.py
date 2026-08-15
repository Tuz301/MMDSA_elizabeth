"""
Role permissions.

These classes answer "may this user perform this kind of action". They do not
answer "on which rows". Row filtering is done by apps.accounts.scoping, and both
checks must pass. Neither is sufficient on its own.
"""

from __future__ import annotations

from rest_framework import permissions

from .models import CLINICAL_DATA_ROLES, USER_ADMIN_ROLES, Role


class IsActiveHealthWorker(permissions.BasePermission):
    message = "This account is not active in the programme."

    def has_permission(self, request, view) -> bool:
        user = request.user
        return bool(user and user.is_authenticated and user.is_active_account)


class CanEnterClinicalData(permissions.BasePermission):
    """
    Enforces the split responsibility workflow.

    A mentor mother may read a clinical record that concerns her own client. She
    may not write one. A laboratory result is entered by a supervisor. The
    mentor mother confirms the follow up action, by SMS reply or in the
    application.

    The split exists because the mentor mother is a peer supporter, not a
    clinician, and because a shared write path would make the audit trail
    unable to say who recorded a result.
    """

    message = (
        "A mentor mother confirms an action but does not record clinical data. "
        "A facility supervisor must enter this."
    )

    def has_permission(self, request, view) -> bool:
        if request.method in permissions.SAFE_METHODS:
            return True
        return Role(request.user.role) in CLINICAL_DATA_ROLES


class CanAdministerUsers(permissions.BasePermission):
    message = "Only a state programme manager or a system administrator may do this."

    def has_permission(self, request, view) -> bool:
        return Role(request.user.role) in USER_ADMIN_ROLES


class IsOwnMentorMotherRecord(permissions.BasePermission):
    """A mentor mother may write only against her own visit records."""

    message = "This record belongs to another mentor mother."

    def has_object_permission(self, request, view, obj) -> bool:
        if Role(request.user.role) != Role.MENTOR_MOTHER:
            return True
        profile = getattr(request.user, "mentor_mother_profile", None)
        return profile is not None and getattr(obj, "mentor_mother_id", None) == profile.id
