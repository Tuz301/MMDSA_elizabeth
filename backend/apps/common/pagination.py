"""Pagination defaults for every list endpoint."""

from __future__ import annotations

from rest_framework.pagination import LimitOffsetPagination


class StandardPagination(LimitOffsetPagination):
    """
    Limit and offset with a hard ceiling.

    The bare LimitOffsetPagination has no max_limit, which would let
    ?limit=100000 dump every patient row a scope allows in one response. A
    caseload screen never needs more than a page or two; a bulk export is a
    supervised administrative act, not an API call.
    """

    default_limit = 50
    max_limit = 200
