"""
Base viewsets that route every queryset through the scoping layer.

A viewset that inherits from these cannot forget row scoping. get_queryset is
the single point every list, retrieve and object-level action passes through,
so a subject outside the caller's scope produces a 404, not a 403 that
confirms the row exists.

No API endpoint exposes DELETE. Removal is a soft delete and an administrative
act done in the Django admin, where the audit middleware already records it.
PUT is also absent: a full replace invites a client to resubmit server-owned
fields, and every partial change PATCH covers.
"""

from __future__ import annotations

from rest_framework import viewsets

from apps.accounts.permissions import IsActiveHealthWorker
from apps.accounts.scoping import scope_queryset


class ScopedModelViewSet(viewsets.ModelViewSet):
    permission_classes = [IsActiveHealthWorker]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        return scope_queryset(super().get_queryset(), self.request.user)


class ScopedReadOnlyModelViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsActiveHealthWorker]

    def get_queryset(self):
        return scope_queryset(super().get_queryset(), self.request.user)
