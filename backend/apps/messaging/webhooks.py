"""
The Termii callback endpoint.

This is the only unauthenticated, internet-facing write path in the system,
and a forged inbound reply can acknowledge an alert — silencing the
escalation chain for an HIV-positive infant. Three rules follow from that.

The signature is the authentication. Termii signs the raw request body with
HMAC-SHA512 using the secret configured in its dashboard and sends the hex
digest in the X-Termii-Signature header. The check runs before the payload is
parsed, over the exact bytes received, in constant time. If the secret is not
configured the check cannot run, and a check that cannot run denies — the
same default the privacy guard applies outbound.

The response gives nothing away. Matched, unmatched and unparsed replies all
receive the same 200 body, so the endpoint cannot be used as an oracle for
"is this telephone number enrolled in an HIV programme". A malformed payload
with a valid signature also gets a 200, because a 4xx makes Termii re-deliver
the same bytes forever.

No log line carries the telephone number or the message text. The payload
byte length, the event type and the provider message id are enough to debug
delivery, and the JSON console logs ship to an aggregator that must never
become a second copy of the patient register.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle

from apps.audit.models import AuditAction, DataClassification
from apps.audit.services import record as audit_record

from .gateway import handle_delivery_report, handle_inbound
from .models import InboundMessage

logger = logging.getLogger(__name__)

#: Refuse a body larger than this before parsing it. A hostile payload must
#: not be able to 500 the view into a Termii retry loop or bloat storage.
MAX_BODY_BYTES = 64 * 1024

_ACCEPTED = {"status": "accepted"}
_IGNORED = {"status": "ignored"}


def _signature_is_valid(request) -> bool:
    secret = settings.TERMII.get("WEBHOOK_SECRET", "")
    if not secret:
        logger.warning(
            "The Termii webhook secret is not configured; all callbacks are refused."
        )
        return False
    supplied = request.headers.get("X-Termii-Signature", "").strip().lower()
    expected = hmac.new(
        secret.encode(), request.body, hashlib.sha512
    ).hexdigest()
    return hmac.compare_digest(supplied, expected)


class TermiiWebhookThrottle(SimpleRateThrottle):
    """
    Rate limit for the callback endpoint, keyed by source address.

    SimpleRateThrottle, not ScopedRateThrottle: the scoped throttle reads its
    scope from a view attribute that a function view does not carry, so a
    ScopedRateThrottle subclass here would silently apply no limit at all to
    the one unauthenticated write path in the system.
    """

    scope = "inbound_sms"

    def get_cache_key(self, request, view) -> str:
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }


# The consumer of this endpoint is Termii, not the client applications,
# so it stays out of the published schema.
@extend_schema(exclude=True)
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([TermiiWebhookThrottle])
def termii_webhook(request):
    """
    POST /api/v1/messaging/webhooks/termii/

    One endpoint for both callback kinds, because the Termii dashboard
    configures a single webhook URL and a single secret. The payload's type
    field says whether this is an inbound reply or a delivery report.
    """
    if len(request.body) > MAX_BODY_BYTES:
        return Response(_IGNORED)

    if not _signature_is_valid(request):
        return Response({"detail": "Invalid signature."}, status=401)

    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning(
            "A signed Termii callback was not JSON. length=%d", len(request.body)
        )
        return Response(_IGNORED)
    if not isinstance(payload, dict):
        return Response(_IGNORED)

    event_type = str(payload.get("type", ""))

    if event_type == "inbound":
        msisdn = str(payload.get("sender") or payload.get("from") or "")
        raw_body = str(
            payload.get("message") or payload.get("sms") or payload.get("text") or ""
        )
        provider_message_id = str(payload.get("message_id") or payload.get("id") or "")
        if not msisdn or not raw_body:
            logger.warning("An inbound callback carried no sender or no text.")
            return Response(_IGNORED)

        # A replayed callback returns the row the first delivery created, and
        # the first delivery already wrote the audit record. Auditing again
        # would count one acknowledgement twice.
        is_replay = bool(provider_message_id) and InboundMessage.objects.filter(
            provider_message_id=provider_message_id[:80]
        ).exists()

        inbound = handle_inbound(
            msisdn=msisdn,
            raw_body=raw_body,
            provider_message_id=provider_message_id[:80],
        )
        if not is_replay and inbound.matched_alert_id and inbound.action_taken:
            # The one programme-significant mutation an unauthenticated actor
            # can perform, so it goes in the audit trail. Field names and the
            # Baby Code only — never the reply text, never the number.
            audit_record(
                action=AuditAction.UPDATE,
                classification=DataClassification.TIER_3_PROGRAMME,
                obj=inbound.matched_alert,
                object_reference=inbound.parsed_code,
                fields=["acknowledged_at", "acknowledgement_channel"],
                justification="Acknowledged by SMS reply.",
            )
        return Response(_ACCEPTED)

    if event_type == "outbound":
        provider_message_id = str(payload.get("message_id") or payload.get("id") or "")
        provider_status = str(payload.get("status") or "")
        if not provider_message_id or not provider_status:
            return Response(_IGNORED)
        handle_delivery_report(provider_message_id, provider_status, payload)
        return Response(_ACCEPTED)

    logger.info("A Termii callback of type %r was ignored.", event_type[:40])
    return Response(_IGNORED)
