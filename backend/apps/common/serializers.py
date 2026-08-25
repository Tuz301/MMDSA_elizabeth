"""
Serializer building blocks shared by every app.

Two concerns live here because every serializer that touches patient data has
them, and repeating either one per app is how one app gets it wrong.

Identifier gating: a direct identifier leaves the database only for a role
that may read it. The gate is applied by removing the field, not blanking it,
so a client cannot mistake an empty string for a cleared value.

Write-side scoping: scoping the read path is not enough. A facility supervisor
could otherwise POST a record whose foreign key points at another site. The
related fields below narrow the candidate rows for a relation to the rows the
requester is entitled to see, so an out-of-scope target fails validation
exactly as an out-of-scope list row fails to appear.
"""

from __future__ import annotations

from rest_framework import serializers

from apps.accounts.scoping import scope_queryset


class IdentifierGatedSerializerMixin:
    """
    Removes direct identifier fields for a role that may not read them.

    The gate is user.may_read_identifiers, the same property the compliance
    matrix keys on. An LGA coordinator or a state manager receives the code
    fields (client_code, baby_code, staff_code) and nothing else.

    With no request in context the fields are pruned. That is the deny-by-
    default direction: a serializer used outside a request cycle leaks
    nothing. The one exception is schema generation, where drf-spectacular
    marks the view with swagger_fake_view; the fields are kept there so the
    published schema documents them.

    Because get_fields is used rather than to_representation, the gate also
    removes the fields from input: a submitted value for a pruned field is
    ignored rather than written.
    """

    identifier_fields: tuple[str, ...] = ()

    def get_fields(self):
        fields = super().get_fields()
        view = self.context.get("view")
        if getattr(view, "swagger_fake_view", False):
            return fields
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated or not user.may_read_identifiers:
            for name in self.identifier_fields:
                fields.pop(name, None)
        return fields


class ScopedPrimaryKeyRelatedField(serializers.PrimaryKeyRelatedField):
    """A related field whose candidate queryset is scoped to the requester."""

    def get_queryset(self):
        queryset = super().get_queryset()
        request = self.context.get("request")
        if request is None or queryset is None:
            # Deny by default, matching the scoping layer.
            return queryset.none() if queryset is not None else None
        return scope_queryset(queryset, request.user)


class ScopedSlugRelatedField(serializers.SlugRelatedField):
    """
    A slug lookup (client_code, baby_code, staff_code) scoped to the requester.

    The error for a code outside the caller's scope is the same as for a code
    that does not exist, so the endpoint cannot be used to test whether a code
    is enrolled at another site.
    """

    def get_queryset(self):
        queryset = super().get_queryset()
        request = self.context.get("request")
        if request is None or queryset is None:
            return queryset.none() if queryset is not None else None
        return scope_queryset(queryset, request.user)
