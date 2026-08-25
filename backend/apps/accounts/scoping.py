"""
Row-level data scoping.

Every list endpoint passes its queryset through scope_queryset before it
returns anything. The function narrows the rows to those the user is entitled
to see.

The default is to return nothing. If a model is not registered in SCOPE_PATHS,
the function returns an empty queryset rather than the full table. A developer
who adds a model and forgets to register it gets an empty list, which is
noticed immediately in testing. The opposite default would silently expose
every patient at every site, and nothing in a test would fail.
"""

from __future__ import annotations

from django.db.models import Q, QuerySet

from .models import Role, User

#: For each model label, the query path from that model to a facility, to an
#: LGA and to a state. A path of None means the model has no link at that level
#: and is therefore visible only at a wider tier.
SCOPE_PATHS: dict[str, dict[str, str | None]] = {
    "registry.Facility": {
        "facility": "id",
        "lga": "lga_id",
        "state": "lga__state_id",
    },
    "registry.MentorMother": {
        "facility": "facility_id",
        "lga": "facility__lga_id",
        "state": "facility__lga__state_id",
    },
    "registry.Client": {
        "facility": "facility_id",
        "lga": "facility__lga_id",
        "state": "facility__lga__state_id",
    },
    "registry.Infant": {
        "facility": "facility_id",
        "lga": "facility__lga_id",
        "state": "facility__lga__state_id",
    },
    "visits.HomeVisit": {
        "facility": "client__facility_id",
        "lga": "client__facility__lga_id",
        "state": "client__facility__lga__state_id",
    },
    "eid.EidAppointment": {
        "facility": "infant__facility_id",
        "lga": "infant__facility__lga_id",
        "state": "infant__facility__lga__state_id",
    },
    "eid.EidSample": {
        "facility": "appointment__infant__facility_id",
        "lga": "appointment__infant__facility__lga_id",
        "state": "appointment__infant__facility__lga__state_id",
    },
    "eid.ArtLinkage": {
        "facility": "infant__facility_id",
        "lga": "infant__facility__lga_id",
        "state": "infant__facility__lga__state_id",
    },
    "alerts.Alert": {
        "facility": "facility_id",
        "lga": "facility__lga_id",
        "state": "facility__lga__state_id",
    },
    "messaging.OutboundMessage": {
        "facility": "facility_id",
        "lga": "facility__lga_id",
        "state": "facility__lga__state_id",
    },
    "registry.State": {
        "facility": "lgas__facilities__id",
        "lga": "lgas__id",
        "state": "id",
    },
    "registry.LocalGovernmentArea": {
        "facility": "facilities__id",
        "lga": "id",
        "state": "state_id",
    },
    "messaging.InboundMessage": {
        # A reply with no matched mentor mother has a null path and drops out
        # of every scoped list. An unattributed reply is reviewed by a system
        # administrator, which is the right audience for it.
        "facility": "mentor_mother__facility_id",
        "lga": "mentor_mother__facility__lga_id",
        "state": "mentor_mother__facility__lga__state_id",
    },
    "visits.GeospatialAnomaly": {
        "facility": "mentor_mother__facility_id",
        "lga": "mentor_mother__facility__lga_id",
        "state": "mentor_mother__facility__lga__state_id",
    },
    "visits.SyncBatch": {
        "facility": "user__facility_id",
        "lga": "user__facility__lga_id",
        "state": "user__facility__lga__state_id",
    },
}

#: Models every authenticated active user may list in full. An entry here is
#: reviewed like a permission change: only a model with no patient rows and no
#: identifier columns qualifies. Every tier must be able to read the alert
#: thresholds and the message templates to predict what the engine will do.
UNSCOPED_CONFIGURATION_MODELS = frozenset(
    {"alerts.AlertRule", "messaging.MessageTemplate"}
)

#: Labels whose scope paths cross a reverse relation, so a filter can match a
#: row more than once. These querysets need distinct() after filtering.
_REVERSE_JOIN_LABELS = frozenset({"registry.State", "registry.LocalGovernmentArea"})


def scope_queryset(queryset: QuerySet, user: User) -> QuerySet:
    """Narrow a queryset to the rows this user is entitled to see."""
    if not user.is_authenticated or not user.is_active_account:
        return queryset.none()

    role = Role(user.role)
    if role == Role.SYSTEM_ADMIN:
        return queryset

    label = queryset.model._meta.label
    if label in UNSCOPED_CONFIGURATION_MODELS:
        return queryset

    paths = SCOPE_PATHS.get(label)
    if paths is None:
        # Unregistered model. Deny by default. See the module docstring.
        return queryset.none()

    if role == Role.MENTOR_MOTHER:
        return _scope_to_mentor_mother(queryset, user, paths)

    if role == Role.FACILITY_SUPERVISOR:
        path = paths["facility"]
        scoped = queryset.filter(**{path: user.facility_id}) if path else queryset.none()
    elif role == Role.LGA_COORDINATOR:
        path = paths["lga"]
        scoped = queryset.filter(**{path: user.lga_id}) if path else queryset.none()
    elif role == Role.STATE_MANAGER:
        path = paths["state"]
        scoped = queryset.filter(**{path: user.state_id}) if path else queryset.none()
    else:
        return queryset.none()

    if label in _REVERSE_JOIN_LABELS:
        scoped = scoped.distinct()
    return scoped


def scope_user_queryset(queryset: QuerySet, user: User) -> QuerySet:
    """
    Scope the user table itself.

    A user row carries exactly one of facility, LGA or state, so a state
    manager's view of the accounts under them is an OR that SCOPE_PATHS cannot
    express. Only the user-admin roles reach the endpoints that call this;
    every other role gets nothing.
    """
    if not user.is_authenticated or not user.is_active_account:
        return queryset.none()

    role = Role(user.role)
    if role == Role.SYSTEM_ADMIN:
        return queryset
    if role == Role.STATE_MANAGER:
        return queryset.filter(
            Q(state_id=user.state_id)
            | Q(lga__state_id=user.state_id)
            | Q(facility__lga__state_id=user.state_id)
        )
    return queryset.none()


def _scope_to_mentor_mother(
    queryset: QuerySet, user: User, paths: dict[str, str | None]
) -> QuerySet:
    """
    A mentor mother sees her own assigned clients and nothing else.

    She does not see the other clients at her facility. That is a narrower
    scope than a facility scope, and it is the tightest scope in the system.
    """
    profile = getattr(user, "mentor_mother_profile", None)
    if profile is None:
        return queryset.none()

    label = queryset.model._meta.label
    own_client_paths = {
        "registry.Client": Q(mentor_mother_id=profile.id),
        "registry.Infant": Q(mother__mentor_mother_id=profile.id),
        "visits.HomeVisit": Q(mentor_mother_id=profile.id),
        "eid.EidAppointment": Q(infant__mother__mentor_mother_id=profile.id),
        "eid.EidSample": Q(appointment__infant__mother__mentor_mother_id=profile.id),
        "eid.ArtLinkage": Q(infant__mother__mentor_mother_id=profile.id),
        "alerts.Alert": Q(assigned_mentor_mother_id=profile.id),
        "registry.MentorMother": Q(id=profile.id),
        "registry.Facility": Q(id=profile.facility_id),
        "registry.LocalGovernmentArea": Q(facilities__id=profile.facility_id),
        "registry.State": Q(lgas__facilities__id=profile.facility_id),
    }
    condition = own_client_paths.get(label)
    if condition is None:
        return queryset.none()
    scoped = queryset.filter(condition)
    if label in _REVERSE_JOIN_LABELS:
        scoped = scoped.distinct()
    return scoped
