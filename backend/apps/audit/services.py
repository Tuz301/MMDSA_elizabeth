"""Helpers that write audit records."""

from __future__ import annotations

from typing import Any

from .middleware import get_request_context
from .models import AuditAction, AuditLog, DataClassification


def record(
    *,
    action: str,
    classification: str = DataClassification.TIER_3_PROGRAMME,
    obj: Any = None,
    object_reference: str = "",
    fields: list[str] | None = None,
    justification: str = "",
    response_status: int | None = None,
) -> AuditLog:
    """
    Write one audit record.

    Never pass a field value into this function. Pass the field name only. See
    the module docstring in apps.audit.models.
    """
    context = get_request_context()
    user = context.get("user")

    return AuditLog.objects.create(
        actor=user,
        actor_username=getattr(user, "username", "") or "anonymous",
        actor_role=getattr(user, "role", ""),
        action=action,
        classification=classification,
        object_type=obj._meta.label if obj is not None else "",
        object_id=str(getattr(obj, "pk", "")) if obj is not None else "",
        object_reference=object_reference,
        fields_touched=fields or [],
        ip_address=context.get("ip_address"),
        user_agent=context.get("user_agent", ""),
        request_path=context.get("path", ""),
        request_method=context.get("method", ""),
        response_status=response_status,
        justification=justification,
    )


def record_identifier_read(obj: Any, fields: list[str], reference: str = "") -> AuditLog:
    """
    Record that a user read a direct identifier.

    Call this from a serialiser when it returns a name, a telephone number or a
    household address. This is the record that makes an inappropriate access
    visible during a review.
    """
    return record(
        action=AuditAction.IDENTIFIER_REVEALED,
        classification=DataClassification.TIER_1_DIRECT_IDENTIFIER,
        obj=obj,
        object_reference=reference,
        fields=fields,
    )
