"""
The Termii SMS gateway.

Every outbound body passes the privacy guard immediately before dispatch, after
the template has been rendered. Checking the template alone is not enough,
because a rendered argument can carry text the template author never saw.

A blocked message is recorded, not silently dropped. A programme manager needs
to know that an alert did not reach anybody, and needs to know why.

The gateway is the single point where the intervention touches the outside
world. If Termii is unreliable on a pilot network, the causal chain in the
theory of change breaks. That is why the failure rate is a programme alarm.
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.utils import timezone

from .guards import PhiGuardError, check_outbound_body, redact_for_log
from .models import DeliveryReport, InboundMessage, OutboundMessage

logger = logging.getLogger(__name__)

#: Termii status values that mean the message will never arrive. A retry on any
#: of these wastes money and does not help.
TERMINAL_FAILURES = {"DND Active on Phone Number", "Rejected", "Message Failed"}


class GatewayError(Exception):
    """The gateway could not accept the message. A retry may succeed."""


def render_body(template_key: str, language: str, arguments: dict[str, Any]) -> str:
    """Render a template. Raises if the template is missing or a key is absent."""
    from .models import MessageTemplate

    template = (
        MessageTemplate.objects.filter(
            key=template_key, language=language, is_active=True
        ).first()
        or MessageTemplate.objects.filter(
            key=template_key, language="en", is_active=True
        ).first()
    )
    if template is None:
        raise GatewayError(f"No active template exists for the key {template_key!r}.")

    try:
        return template.body.format(**arguments)
    except KeyError as exc:
        raise GatewayError(
            f"The template {template_key!r} needs the argument {exc.args[0]!r}, "
            f"which was not supplied."
        ) from exc


def dispatch(message_id: str, task=None) -> dict:
    """
    Send one queued message.

    Called by the Celery task. The task holds the retry policy; this function
    decides whether a retry is worth attempting.
    """
    message = OutboundMessage.objects.select_related("template").get(pk=message_id)

    if message.status not in {OutboundMessage.Status.QUEUED}:
        return {"skipped": True, "status": message.status}

    language = message.template.language if message.template else "en"
    try:
        body = render_body(message.template_key, language, message.render_arguments)
    except GatewayError as exc:
        message.status = OutboundMessage.Status.FAILED
        message.last_error = str(exc)[:300]
        message.save(update_fields=["status", "last_error", "updated_at"])
        logger.error("Could not render message %s: %s", message_id, exc)
        return {"sent": False, "reason": "render failed"}

    # The privacy guard. Nothing gets past this.
    if settings.TERMII["ENFORCE_PHI_GUARD"]:
        result = check_outbound_body(body)
        if not result.passed:
            message.status = OutboundMessage.Status.BLOCKED
            message.guard_violations = result.violations
            message.save(
                update_fields=["status", "guard_violations", "updated_at"]
            )
            logger.error(
                "A message was blocked before dispatch. template=%s violations=%s",
                message.template_key,
                result.violations,
            )
            return {"sent": False, "reason": "blocked by the privacy guard"}

    message.body_length = len(body)
    message.segment_count = max(1, -(-len(body) // 160))
    message.attempt_count += 1

    try:
        response = _post_to_termii(message.recipient_msisdn, body)
    except GatewayError as exc:
        message.last_error = str(exc)[:300]
        message.status = OutboundMessage.Status.QUEUED
        message.save(
            update_fields=[
                "attempt_count", "last_error", "status", "body_length",
                "segment_count", "updated_at",
            ]
        )
        if task is not None and message.attempt_count < settings.TERMII["MAX_RETRIES"]:
            raise task.retry(exc=exc)
        message.status = OutboundMessage.Status.FAILED
        message.save(update_fields=["status", "updated_at"])
        return {"sent": False, "reason": str(exc)}

    message.status = OutboundMessage.Status.SENT
    message.sent_at = timezone.now()
    message.provider_message_id = response.get("message_id", "")
    message.provider_status_detail = str(response.get("message", ""))[:200]
    message.save(
        update_fields=[
            "status", "sent_at", "provider_message_id", "provider_status_detail",
            "attempt_count", "body_length", "segment_count", "updated_at",
        ]
    )
    logger.info(
        "Message sent. template=%s %s",
        message.template_key,
        redact_for_log(body),
    )
    return {"sent": True, "provider_message_id": message.provider_message_id}


def _post_to_termii(msisdn: str, body: str) -> dict:
    """Call the Termii send endpoint."""
    import json
    import urllib.error
    import urllib.request

    cfg = settings.TERMII
    if not cfg["API_KEY"]:
        raise GatewayError("No Termii API key is configured.")

    payload = json.dumps(
        {
            "to": msisdn,
            "from": cfg["SENDER_ID"],
            "sms": body,
            "type": "plain",
            "channel": cfg["CHANNEL"],
            "api_key": cfg["API_KEY"],
        }
    ).encode()

    request = urllib.request.Request(
        f"{cfg['BASE_URL']}/api/sms/send",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=cfg["TIMEOUT_SECONDS"]
        ) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raise GatewayError(f"Termii returned HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise GatewayError(f"Could not reach Termii: {exc.reason}.") from exc
    except json.JSONDecodeError as exc:
        raise GatewayError("Termii returned a response that was not JSON.") from exc


# ---------------------------------------------------------------------------
# Inbound
# ---------------------------------------------------------------------------


def parse_reply(raw_body: str) -> tuple[str, str]:
    """
    Extract the keyword and the code from a reply.

    A reply is expected to look like "DONE BX7K2MQ". Real replies are messier:
    lower case, extra words, a missing code, a code with a transposed
    character. The parser is deliberately forgiving about everything except the
    code itself, which must match exactly, because acting on the wrong infant is
    worse than failing to parse.
    """
    import re

    from apps.registry.models import Infant

    tokens = re.findall(r"[A-Za-z0-9]+", raw_body.upper())
    if not tokens:
        return "", ""

    known_keywords = {"DONE", "SEEN", "YES", "NO", "OK", "HELP", "STOP", "LATER"}
    keyword = next((token for token in tokens if token in known_keywords), "")

    code = ""
    for token in tokens:
        if token.startswith("B") and len(token) == 8:
            if Infant.objects.filter(baby_code=token).exists():
                code = token
                break

    return keyword, code


def handle_inbound(msisdn: str, raw_body: str, provider_message_id: str = "") -> InboundMessage:
    """
    Record an inbound reply and apply its effect.

    A reply that matches an open alert acknowledges that alert. This is how a
    mentor mother without a smartphone closes the loop.
    """
    from apps.alerts.models import Alert
    from apps.registry.models import Infant, MentorMother

    keyword, code = parse_reply(raw_body)

    inbound = InboundMessage(
        sender_msisdn=msisdn,
        raw_body=raw_body[:320],
        parsed_keyword=keyword,
        parsed_code=code,
        provider_message_id=provider_message_id,
    )

    if not keyword and not code:
        inbound.parse_status = InboundMessage.ParseStatus.UNPARSED
        inbound.save()
        return inbound

    # A telephone number is encrypted, so it cannot be matched with a database
    # filter. Match on the most recent message sent to this number instead.
    recent = (
        OutboundMessage.objects.filter(
            expects_reply=True, reply_received_at__isnull=True
        )
        .order_by("-sent_at")[:200]
    )
    match = next((m for m in recent if m.recipient_msisdn == msisdn), None)

    if match is None:
        inbound.parse_status = InboundMessage.ParseStatus.UNMATCHED
        inbound.save()
        logger.info("An inbound reply matched no outstanding message. keyword=%s", keyword)
        return inbound

    inbound.matched_message = match
    inbound.parse_status = InboundMessage.ParseStatus.MATCHED

    if match.recipient_reference:
        inbound.mentor_mother = MentorMother.objects.filter(
            staff_code=match.recipient_reference
        ).first()

    if match.alert_id and keyword in {"DONE", "SEEN", "YES", "OK"}:
        alert = Alert.objects.filter(pk=match.alert_id).first()
        if alert is not None:
            alert.acknowledge(user=None, channel="SMS")
            inbound.matched_alert = alert
            inbound.action_taken = f"Acknowledged alert {alert.pk} by SMS reply."

    inbound.save()

    match.reply_received_at = timezone.now()
    match.save(update_fields=["reply_received_at", "updated_at"])
    return inbound


def reconcile() -> dict:
    """
    Apply delivery reports that arrived before their message row was updated,
    and expire messages the gateway never confirmed.
    """
    from datetime import timedelta

    applied = 0
    orphans = DeliveryReport.objects.filter(message__isnull=True)
    for report in orphans:
        message = OutboundMessage.objects.filter(
            provider_message_id=report.provider_message_id
        ).first()
        if message is None:
            continue
        report.message = message
        report.save(update_fields=["message"])
        _apply_status(message, report.provider_status)
        applied += 1

    stale_cutoff = timezone.now() - timedelta(hours=24)
    expired = OutboundMessage.objects.filter(
        status=OutboundMessage.Status.SENT, sent_at__lt=stale_cutoff
    ).update(status=OutboundMessage.Status.EXPIRED, updated_at=timezone.now())

    return {"reports_applied": applied, "messages_expired": expired}


def _apply_status(message: OutboundMessage, provider_status: str) -> None:
    if provider_status in {"Delivered", "DELIVERED"}:
        message.status = OutboundMessage.Status.DELIVERED
        message.delivered_at = timezone.now()
    elif provider_status in TERMINAL_FAILURES:
        message.status = OutboundMessage.Status.FAILED
    message.provider_status_detail = provider_status[:200]
    message.save(
        update_fields=["status", "delivered_at", "provider_status_detail", "updated_at"]
    )
