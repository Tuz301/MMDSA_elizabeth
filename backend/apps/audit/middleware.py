"""
Request context for the audit trail.

The acting user is known in the view layer but not in the model layer. This
middleware puts the current request into a context variable so that a signal
handler or a model save can record who acted, without every function having to
pass the request down.

A context variable is used rather than thread local storage because it behaves
correctly under async views and under Celery.
"""

from __future__ import annotations

import contextvars
from typing import Any

_request_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "mmdsa_request_context", default=None
)


def get_request_context() -> dict[str, Any]:
    return _request_context.get() or {}


def set_request_context(context: dict[str, Any]) -> None:
    _request_context.set(context)


class AuditContextMiddleware:
    """Records the acting user, the client address and the request path."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        set_request_context(
            {
                "user": user if getattr(user, "is_authenticated", False) else None,
                "ip_address": self._client_ip(request),
                "user_agent": request.META.get("HTTP_USER_AGENT", "")[:300],
                "path": request.path[:200],
                "method": request.method,
            }
        )
        try:
            return self.get_response(request)
        finally:
            set_request_context({})

    @staticmethod
    def _client_ip(request) -> str | None:
        # The application sits behind an Application Load Balancer, so the
        # client address is the first entry in the forwarded header. Trust it
        # only because the security group allows traffic from the balancer
        # alone.
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR")
