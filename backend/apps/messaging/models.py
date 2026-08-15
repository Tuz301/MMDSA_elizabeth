"""
SMS templates, outbound messages and inbound replies.

The two way SMS loop is the mechanism the whole intervention rests on. A
supervisor enters a result, the engine raises an alert, the mentor mother
receives a message, she acts, and she replies with a keyword. Her reply closes
the loop and is the evidence that the relay worked.

If SMS delivery is unreliable on any pilot network, the causal chain in the
theory of change breaks and no amount of good architecture repairs it. Every
message therefore carries its full delivery history, and the delivery failure
rate is a programme alarm rather than an infrastructure metric.

Message bodies are stored redacted. The template key and the code arguments are
kept, so a message can be reconstructed for an audit, but the rendered body is
not held a second time in this table.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone

from apps.common.fields import EncryptedCharField
from apps.common.models import BaseModel


class MessageTemplate(BaseModel):
    """
    An SMS template.

    Every template is checked by the guard when it is saved. A template that
    would insert a name or a clinical word cannot be stored.
    """

    class Language(models.TextChoices):
        ENGLISH = "en", "English"
        HAUSA = "ha", "Hausa"
        YORUBA = "yo", "Yoruba"
        IGBO = "ig", "Igbo"
        PIDGIN = "pcm", "Nigerian Pidgin"

    key = models.CharField(
        max_length=40, help_text="Referenced by an alert rule, for example eid_due."
    )
    language = models.CharField(
        max_length=4, choices=Language.choices, default=Language.ENGLISH
    )
    body = models.TextField(
        max_length=320,
        help_text=(
            "Use {baby_code}, {client_code}, {staff_code}, {due_date_words}. "
            "A placeholder that would insert a name is rejected."
        ),
    )
    expects_reply = models.BooleanField(default=False)
    reply_keywords = models.JSONField(
        default=list,
        blank=True,
        help_text='Accepted replies, for example ["DONE", "SEEN", "NO"].',
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["key", "language"]
        unique_together = [("key", "language")]

    def __str__(self) -> str:
        return f"{self.key} [{self.language}]"

    def clean(self) -> None:
        from .guards import check_template_placeholders

        super().clean()
        result = check_template_placeholders(self.body)
        if not result.passed:
            from django.core.exceptions import ValidationError

            raise ValidationError({"body": result.violations})


class OutboundMessage(BaseModel):
    """One SMS sent through the gateway."""

    class Status(models.TextChoices):
        QUEUED = "QUEUED", "Queued"
        SENT = "SENT", "Handed to the gateway"
        DELIVERED = "DELIVERED", "Delivered to the handset"
        FAILED = "FAILED", "Delivery failed"
        BLOCKED = "BLOCKED", "Blocked by the privacy guard"
        EXPIRED = "EXPIRED", "The gateway gave up"

    class Recipient(models.TextChoices):
        MENTOR_MOTHER = "MENTOR_MOTHER", "Mentor mother"
        CLIENT = "CLIENT", "Client"
        SUPERVISOR = "SUPERVISOR", "Facility supervisor"

    template = models.ForeignKey(
        MessageTemplate,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="messages",
    )
    alert = models.ForeignKey(
        "alerts.Alert",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages",
    )
    facility = models.ForeignKey(
        "registry.Facility", on_delete=models.PROTECT, related_name="messages"
    )

    recipient_kind = models.CharField(max_length=16, choices=Recipient.choices)
    recipient_msisdn = EncryptedCharField(max_length=20)
    recipient_reference = models.CharField(
        max_length=24,
        blank=True,
        help_text="Staff code or client code of the recipient. Never a name.",
    )

    # The rendered body is not stored. The template key and the arguments are
    # enough to rebuild it for an audit, and holding one copy is safer than two.
    template_key = models.CharField(max_length=40)
    render_arguments = models.JSONField(default=dict, blank=True)
    body_length = models.PositiveSmallIntegerField(default=0)
    segment_count = models.PositiveSmallIntegerField(default=1)

    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.QUEUED, db_index=True
    )
    guard_violations = models.JSONField(
        default=list,
        blank=True,
        help_text="Populated when the privacy guard blocked the message.",
    )

    provider_message_id = models.CharField(max_length=80, blank=True, db_index=True)
    provider_status_detail = models.CharField(max_length=200, blank=True)
    cost_naira = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True
    )

    queued_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    last_error = models.CharField(max_length=300, blank=True)

    expects_reply = models.BooleanField(default=False)
    reply_received_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-queued_at"]
        indexes = [
            models.Index(fields=["status", "queued_at"]),
            models.Index(fields=["facility", "status"]),
            models.Index(fields=["expects_reply", "reply_received_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.template_key} to {self.recipient_reference} [{self.status}]"

    @property
    def delivery_latency_seconds(self) -> float | None:
        if self.sent_at and self.delivered_at:
            return (self.delivered_at - self.sent_at).total_seconds()
        return None

    @property
    def is_awaiting_reply(self) -> bool:
        return self.expects_reply and self.reply_received_at is None


class InboundMessage(BaseModel):
    """
    A reply from a mentor mother or a client.

    A mentor mother without a smartphone works entirely through this channel.
    She receives a structured message and replies with a keyword and a code.
    That reply is what confirms an action in the split responsibility workflow.

    An unmatched reply is kept rather than discarded. A pattern of unmatched
    replies usually means the instruction text is unclear, and that is
    information the programme needs.
    """

    class ParseStatus(models.TextChoices):
        MATCHED = "MATCHED", "Parsed and linked to a message"
        UNMATCHED = "UNMATCHED", "Parsed but no matching message was found"
        UNPARSED = "UNPARSED", "The reply could not be interpreted"
        IGNORED = "IGNORED", "Not a programme reply"

    received_at = models.DateTimeField(default=timezone.now, db_index=True)
    sender_msisdn = EncryptedCharField(max_length=20)
    raw_body = models.CharField(
        max_length=320,
        help_text=(
            "Held in clear. A reply is a keyword and a code, and no message "
            "sent by this system ever invites a person to type an identifier."
        ),
    )

    parse_status = models.CharField(
        max_length=10,
        choices=ParseStatus.choices,
        default=ParseStatus.UNPARSED,
        db_index=True,
    )
    parsed_keyword = models.CharField(max_length=20, blank=True)
    parsed_code = models.CharField(max_length=24, blank=True)

    matched_message = models.ForeignKey(
        OutboundMessage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replies",
    )
    matched_alert = models.ForeignKey(
        "alerts.Alert",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sms_replies",
    )
    mentor_mother = models.ForeignKey(
        "registry.MentorMother",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sms_replies",
    )

    action_taken = models.CharField(
        max_length=120,
        blank=True,
        help_text="What the reply caused, for example an alert acknowledgement.",
    )
    provider_message_id = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ["-received_at"]
        indexes = [models.Index(fields=["parse_status", "received_at"])]

    def __str__(self) -> str:
        return f"{self.parsed_keyword or 'unparsed'} at {self.received_at:%Y-%m-%d %H:%M}"


class DeliveryReport(BaseModel):
    """
    A raw delivery notification from the gateway.

    Every callback is kept, including one that arrives out of order or names an
    unknown message. When SMS delivery is questioned during the evaluation, this
    table is the evidence.
    """

    message = models.ForeignKey(
        OutboundMessage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="delivery_reports",
    )
    provider_message_id = models.CharField(max_length=80, db_index=True)
    provider_status = models.CharField(max_length=40)
    provider_payload = models.JSONField(default=dict, blank=True)
    received_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-received_at"]

    def __str__(self) -> str:
        return f"{self.provider_message_id}: {self.provider_status}"
