"""
The Termii webhook: the only unauthenticated write path in the system.

The tests use a plain unauthenticated client on purpose. The endpoint's whole
contract is that it works without a user and fails without a signature.
"""

import hashlib
import hmac
import json

import pytest
from rest_framework.test import APIClient

from apps.messaging.models import DeliveryReport, InboundMessage, OutboundMessage

WEBHOOK_URL = "/api/v1/messaging/webhooks/termii/"
SECRET = "test-webhook-secret"


@pytest.fixture
def webhook_secret(settings):
    """Pin a webhook secret so a signed request can be constructed."""
    settings.TERMII = {**settings.TERMII, "WEBHOOK_SECRET": SECRET}
    return SECRET


def _signed_post(payload, *, secret=SECRET, signature=None, raw=None):
    client = APIClient()
    body = raw if raw is not None else json.dumps(payload).encode()
    if signature is None:
        signature = hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()
    return client.post(
        WEBHOOK_URL,
        data=body,
        content_type="application/json",
        HTTP_X_TERMII_SIGNATURE=signature,
    )


def _inbound_payload(text="DONE", message_id="tm-001", sender="+2348010000000"):
    return {"type": "inbound", "sender": sender, "message": text, "message_id": message_id}


@pytest.mark.django_db
class TestSignature:
    def test_a_correctly_signed_inbound_payload_is_accepted(self, webhook_secret):
        response = _signed_post(_inbound_payload())
        assert response.status_code == 200
        assert InboundMessage.objects.count() == 1

    def test_a_wrong_signature_is_refused_and_stores_nothing(self, webhook_secret):
        response = _signed_post(_inbound_payload(), signature="0" * 128)
        assert response.status_code == 401
        assert InboundMessage.objects.count() == 0

    def test_a_missing_signature_header_is_refused(self, webhook_secret):
        client = APIClient()
        response = client.post(
            WEBHOOK_URL,
            data=json.dumps(_inbound_payload()),
            content_type="application/json",
        )
        assert response.status_code == 401

    def test_with_no_secret_configured_every_callback_is_refused(self, settings):
        # Fail closed: a check that cannot run denies. Even a request signed
        # with the would-be secret is refused while the secret is unset.
        settings.TERMII = {**settings.TERMII, "WEBHOOK_SECRET": ""}
        response = _signed_post(_inbound_payload())
        assert response.status_code == 401
        assert InboundMessage.objects.count() == 0

    def test_tampering_one_byte_after_signing_is_refused(self, webhook_secret):
        body = json.dumps(_inbound_payload()).encode()
        signature = hmac.new(SECRET.encode(), body, hashlib.sha512).hexdigest()
        tampered = body.replace(b"DONE", b"SEEN")
        response = _signed_post(None, raw=tampered, signature=signature)
        assert response.status_code == 401


@pytest.mark.django_db
class TestIdempotentReplay:
    def test_a_replayed_callback_creates_one_row_and_one_acknowledgement(
        self, webhook_secret, sent_message, open_alert
    ):
        payload = _inbound_payload(text="DONE", message_id="tm-replay")
        first = _signed_post(payload)
        assert first.status_code == 200
        open_alert.refresh_from_db()
        acknowledged_at = open_alert.acknowledged_at
        assert acknowledged_at is not None

        second = _signed_post(payload)
        assert second.status_code == 200
        assert InboundMessage.objects.count() == 1
        open_alert.refresh_from_db()
        assert open_alert.acknowledged_at == acknowledged_at

    def test_calls_without_a_provider_id_still_create_separate_rows(self, db):
        # Protects the existing gateway tests' assumption: the dedupe guard
        # triggers only on a non-empty provider id.
        from apps.messaging.gateway import handle_inbound

        handle_inbound("+2348010000000", "hello")
        handle_inbound("+2348010000000", "hello")
        assert InboundMessage.objects.count() == 2


@pytest.mark.django_db
class TestInboundMatching:
    def test_a_reply_acknowledges_the_matched_alert(
        self, webhook_secret, sent_message, open_alert
    ):
        response = _signed_post(_inbound_payload(text="DONE"))
        assert response.status_code == 200
        inbound = InboundMessage.objects.get()
        assert inbound.parse_status == InboundMessage.ParseStatus.MATCHED
        open_alert.refresh_from_db()
        assert open_alert.status == open_alert.Status.ACKNOWLEDGED
        assert open_alert.acknowledgement_channel == "SMS"
        sent_message.refresh_from_db()
        assert sent_message.reply_received_at is not None

    def test_a_reply_from_an_unknown_number_is_kept_as_unmatched(
        self, webhook_secret, sent_message
    ):
        response = _signed_post(
            _inbound_payload(text="DONE", sender="+2348099999999")
        )
        assert response.status_code == 200
        inbound = InboundMessage.objects.get()
        assert inbound.parse_status == InboundMessage.ParseStatus.UNMATCHED

    def test_the_response_is_identical_for_matched_and_unmatched_senders(
        self, webhook_secret, sent_message
    ):
        # The enrolment-oracle test. If the two responses differ in any way,
        # the endpoint can be used to test whether a telephone number is
        # enrolled in an HIV programme.
        matched = _signed_post(_inbound_payload(text="DONE", message_id="tm-a"))
        unmatched = _signed_post(
            _inbound_payload(
                text="DONE", message_id="tm-b", sender="+2348099999999"
            )
        )
        assert matched.status_code == unmatched.status_code == 200
        assert matched.json() == unmatched.json()

    def test_the_response_never_carries_the_number_or_the_text(
        self, webhook_secret, sent_message
    ):
        response = _signed_post(_inbound_payload(text="DONE B2345678"))
        body = response.content.decode()
        assert "+234" not in body
        assert "DONE" not in body
        assert "B2345678" not in body


@pytest.mark.django_db
class TestDeliveryReports:
    def _dlr(self, message_id, status="Delivered"):
        return {
            "type": "outbound",
            "message_id": message_id,
            "status": status,
            "receiver": "+2348010000000",
            "message": "IHVN: Baby B2345678 needs a visit.",
        }

    def test_a_report_for_a_known_message_marks_it_delivered(
        self, webhook_secret, sent_message
    ):
        sent_message.provider_message_id = "pm-1"
        sent_message.save(update_fields=["provider_message_id"])
        response = _signed_post(self._dlr("pm-1"))
        assert response.status_code == 200
        sent_message.refresh_from_db()
        assert sent_message.status == OutboundMessage.Status.DELIVERED
        assert sent_message.delivered_at is not None

    def test_a_terminal_failure_marks_the_message_failed(
        self, webhook_secret, sent_message
    ):
        sent_message.provider_message_id = "pm-2"
        sent_message.save(update_fields=["provider_message_id"])
        _signed_post(self._dlr("pm-2", status="Rejected"))
        sent_message.refresh_from_db()
        assert sent_message.status == OutboundMessage.Status.FAILED

    def test_an_orphan_report_is_kept_and_reconcile_attaches_it(
        self, webhook_secret, facility, mentor_mother
    ):
        from apps.messaging.gateway import reconcile

        _signed_post(self._dlr("pm-orphan"))
        report = DeliveryReport.objects.get()
        assert report.message is None

        message = OutboundMessage.objects.create(
            facility=facility,
            recipient_kind="MENTOR_MOTHER",
            recipient_msisdn="+2348010000000",
            recipient_reference=mentor_mother.staff_code,
            template_key="eid_reminder",
            provider_message_id="pm-orphan",
            status=OutboundMessage.Status.SENT,
        )
        result = reconcile()
        assert result["reports_applied"] == 1
        message.refresh_from_db()
        assert message.status == OutboundMessage.Status.DELIVERED

    def test_a_byte_identical_duplicate_creates_no_second_row(self, webhook_secret):
        _signed_post(self._dlr("pm-dup"))
        _signed_post(self._dlr("pm-dup"))
        assert DeliveryReport.objects.count() == 1

    def test_a_different_status_for_the_same_id_is_kept(self, webhook_secret):
        # Every callback is kept: a message legitimately moves through
        # several statuses and the evaluation needs the whole history.
        _signed_post(self._dlr("pm-seq", status="Sent"))
        _signed_post(self._dlr("pm-seq", status="Delivered"))
        assert DeliveryReport.objects.count() == 2

    def test_the_stored_payload_is_scrubbed_of_personal_fields(self, webhook_secret):
        _signed_post(self._dlr("pm-scrub"))
        payload = DeliveryReport.objects.get().provider_payload
        assert "receiver" not in payload
        assert "message" not in payload
        assert "sender" not in payload


@pytest.mark.django_db
class TestHygiene:
    def test_malformed_json_with_a_valid_signature_returns_200_and_stores_nothing(
        self, webhook_secret
    ):
        # A 4xx would make Termii re-deliver the same malformed bytes forever.
        raw = b"this is not json"
        signature = hmac.new(SECRET.encode(), raw, hashlib.sha512).hexdigest()
        response = _signed_post(None, raw=raw, signature=signature)
        assert response.status_code == 200
        assert InboundMessage.objects.count() == 0
        assert DeliveryReport.objects.count() == 0

    def test_an_unknown_event_type_is_ignored(self, webhook_secret):
        response = _signed_post({"type": "device_status", "battery": "low"})
        assert response.status_code == 200
        assert InboundMessage.objects.count() == 0

    def test_no_log_line_carries_the_number_or_the_reply(
        self, webhook_secret, sent_message, caplog
    ):
        # The cheapest test in the file and the one most worth keeping green:
        # the JSON console logs ship to an aggregator, and the aggregator
        # must never become a second copy of the patient register.
        with caplog.at_level("DEBUG"):
            _signed_post(_inbound_payload(text="DONE B2345678"))
            _signed_post(
                _inbound_payload(text="SEEN", sender="+2348099999999", message_id="x2")
            )
        for record in caplog.records:
            message = record.getMessage()
            assert "+234" not in message
            assert "B2345678" not in message
