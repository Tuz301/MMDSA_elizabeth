"""Queueing helpers for outbound messages."""

from __future__ import annotations

from typing import Any

from .models import MessageTemplate, OutboundMessage


def queue_templated_message(
    *,
    template_key: str,
    recipient_kind: str,
    msisdn: str,
    recipient_reference: str,
    facility,
    arguments: dict[str, Any],
    alert=None,
    language: str = "en",
) -> OutboundMessage:
    """
    Create a queued message and hand it to the dispatch task.

    The body is not rendered here. It is rendered inside the task, immediately
    before the privacy guard runs, so that the guard always inspects the exact
    text that would go out.
    """
    template = MessageTemplate.objects.filter(
        key=template_key, language=language, is_active=True
    ).first()

    message = OutboundMessage.objects.create(
        template=template,
        alert=alert,
        facility=facility,
        recipient_kind=recipient_kind,
        recipient_msisdn=msisdn,
        recipient_reference=recipient_reference,
        template_key=template_key,
        render_arguments=arguments,
        expects_reply=bool(template and template.expects_reply),
    )

    from .tasks import send_message

    send_message.delay(str(message.pk))
    return message


#: Starting template set. Every body is written to survive the privacy guard:
#: no clinical word, no name, no date in numeric form.
DEFAULT_TEMPLATES = [
    {
        "key": "eid_reminder",
        "language": "en",
        "body": (
            "IHVN: Baby {baby_code} has a clinic appointment on "
            "{due_date_words}. Please support the family to attend. "
            "Reply SEEN to confirm."
        ),
        "expects_reply": True,
        "reply_keywords": ["SEEN", "NO"],
    },
    {
        "key": "urgent_follow_up",
        "language": "en",
        "body": (
            "IHVN URGENT: Baby {baby_code} needs a same-day clinic visit. "
            "Please contact the family now and reply DONE when you have."
        ),
        "expects_reply": True,
        "reply_keywords": ["DONE", "HELP"],
    },
    {
        "key": "linkage_overdue",
        "language": "en",
        "body": (
            "IHVN: Baby {baby_code} still needs to attend the clinic. "
            "Please follow up today and reply DONE."
        ),
        "expects_reply": True,
        "reply_keywords": ["DONE", "HELP"],
    },
    {
        "key": "appointment_missed",
        "language": "en",
        "body": (
            "IHVN: Baby {baby_code} did not attend on {due_date_words}. "
            "Please visit the family and reply DONE."
        ),
        "expects_reply": True,
        "reply_keywords": ["DONE", "NO"],
    },
    {
        "key": "sync_reminder",
        "language": "en",
        "body": (
            "IHVN: Your records have not reached the office. Please open the "
            "app where there is network so it can send them."
        ),
        "expects_reply": False,
        "reply_keywords": [],
    },
    {
        "key": "eid_reminder",
        "language": "ha",
        "body": (
            "IHVN: Jariri {baby_code} yana da alkawari a asibiti ranar "
            "{due_date_words}. Ka taimaka iyali su zo. Amsa da SEEN."
        ),
        "expects_reply": True,
        "reply_keywords": ["SEEN", "NO"],
    },
]
