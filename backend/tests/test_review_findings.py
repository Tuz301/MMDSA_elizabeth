"""
Regression tests for the adversarial review findings.

Each test pins a bug the council's review confirmed, so a reintroduction
fails with the finding's name in the test id.
"""

import datetime as dt

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.alerts.models import Alert
from apps.eid.models import ArtLinkage, EidAppointment, EidSample
from apps.registry.models import Infant


def _client_for(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


@pytest.fixture
def positive_entered(api_client, infant):
    """A positive result entered through the API, with its linkage open."""
    appointment = EidAppointment.objects.create(
        infant=infant,
        milestone="BIRTH",
        due_date=dt.date.today() - dt.timedelta(days=30),
    )
    sample = EidSample.objects.create(
        appointment=appointment,
        sample_identifier="SMP-RF-001",
        collected_on=dt.date.today() - dt.timedelta(days=20),
    )
    api_client.post(
        f"/api/v1/eid/samples/{sample.pk}/result/",
        {"result": "POSITIVE", "result_issued_on": str(dt.date.today() - dt.timedelta(days=10))},
        format="json",
    )
    sample.refresh_from_db()
    return sample


@pytest.mark.django_db
class TestConfirmatoryPositiveDoesNotRegressOutcome:
    def test_a_second_positive_leaves_an_on_art_infant_on_art(
        self, api_client, infant, positive_entered
    ):
        linkage = ArtLinkage.objects.get(infant=infant)
        api_client.patch(
            f"/api/v1/eid/linkages/{linkage.pk}/",
            {"status": "STARTED", "art_start_date": str(dt.date.today())},
            format="json",
        )
        infant.refresh_from_db()
        assert infant.outcome == Infant.Outcome.HIV_POSITIVE_ON_ART

        # A confirmatory positive at a later milestone.
        appointment = EidAppointment.objects.create(
            infant=infant,
            milestone="MONTH_9",
            due_date=dt.date.today(),
        )
        confirmatory = EidSample.objects.create(
            appointment=appointment,
            sample_identifier="SMP-RF-002",
            collected_on=dt.date.today(),
        )
        response = api_client.post(
            f"/api/v1/eid/samples/{confirmatory.pk}/result/",
            {"result": "POSITIVE", "result_issued_on": str(dt.date.today())},
            format="json",
        )
        assert response.status_code == 200
        infant.refresh_from_db()
        # The linkage record is the authority on treatment state.
        assert infant.outcome == Infant.Outcome.HIV_POSITIVE_ON_ART
        assert ArtLinkage.objects.filter(infant=infant).count() == 1


@pytest.mark.django_db
class TestAlertAcknowledgementIsWriteOnce:
    def test_a_second_acknowledgement_does_not_rewrite_the_timestamp(
        self, api_client, open_alert
    ):
        api_client.post(f"/api/v1/alerts/{open_alert.pk}/acknowledge/")
        open_alert.refresh_from_db()
        first_at = open_alert.acknowledged_at
        first_by = open_alert.acknowledged_by

        response = api_client.post(f"/api/v1/alerts/{open_alert.pk}/acknowledge/")
        assert response.status_code == 200
        open_alert.refresh_from_db()
        assert open_alert.acknowledged_at == first_at
        assert open_alert.acknowledged_by == first_by

    def test_an_sms_reply_after_an_app_acknowledgement_changes_nothing(
        self, api_client, open_alert, supervisor
    ):
        api_client.post(f"/api/v1/alerts/{open_alert.pk}/acknowledge/")
        open_alert.refresh_from_db()
        first_at = open_alert.acknowledged_at

        open_alert.acknowledge(user=None, channel="SMS")
        open_alert.refresh_from_db()
        assert open_alert.acknowledged_at == first_at
        assert open_alert.acknowledged_by == supervisor
        assert open_alert.acknowledgement_channel == "APP"


@pytest.mark.django_db
class TestFrozenClinicalRelations:
    def test_a_sample_cannot_be_repointed_at_another_appointment(
        self, api_client, positive_entered, infant
    ):
        other = EidAppointment.objects.create(
            infant=infant,
            milestone="WEEK_6",
            due_date=dt.date.today(),
        )
        response = api_client.patch(
            f"/api/v1/eid/samples/{positive_entered.pk}/",
            {"appointment": str(other.pk)},
            format="json",
        )
        assert response.status_code == 400
        positive_entered.refresh_from_db()
        assert positive_entered.appointment.milestone == "BIRTH"

    def test_a_duplicate_linkage_post_is_a_400_not_a_500(
        self, api_client, positive_entered, infant
    ):
        assert ArtLinkage.objects.filter(infant=infant).exists()
        response = api_client.post(
            "/api/v1/eid/linkages/",
            {
                "infant": infant.baby_code,
                "triggering_sample": str(positive_entered.pk),
                "status": "PENDING",
            },
            format="json",
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestRoleEscalation:
    def test_a_state_manager_cannot_mint_a_system_admin(self, facility):
        state_manager = User.objects.create(
            username="sm-test",
            role=Role.STATE_MANAGER,
            state=facility.lga.state,
            cognito_sub="test-sub-sm",
        )
        for forbidden in (Role.SYSTEM_ADMIN, Role.STATE_MANAGER):
            response = _client_for(state_manager).post(
                "/api/v1/accounts/users/",
                {"username": f"new-{forbidden}".lower(), "role": forbidden},
                format="json",
            )
            assert response.status_code == 400, forbidden

    def test_a_state_manager_creates_a_supervisor_below_their_tier(self, facility):
        state_manager = User.objects.create(
            username="sm-test-2",
            role=Role.STATE_MANAGER,
            state=facility.lga.state,
            cognito_sub="test-sub-sm2",
        )
        response = _client_for(state_manager).post(
            "/api/v1/accounts/users/",
            {
                "username": "new-supervisor",
                "role": Role.FACILITY_SUPERVISOR,
                "facility": str(facility.pk),
            },
            format="json",
        )
        assert response.status_code == 201, response.content


@pytest.mark.django_db
class TestSummaryHygiene:
    def test_a_malformed_date_is_a_400_not_a_500(self, api_client):
        response = api_client.get("/api/v1/metrics/summary/?date_from=notadate")
        assert response.status_code == 400


@pytest.mark.django_db
class TestWebhookAuditOnce:
    def test_a_replayed_callback_writes_one_audit_record(
        self, settings, sent_message, open_alert
    ):
        import hashlib
        import hmac
        import json

        from apps.audit.models import AuditLog

        settings.TERMII = {**settings.TERMII, "WEBHOOK_SECRET": "test-secret"}
        payload = json.dumps(
            {
                "type": "inbound",
                "sender": "+2348010000000",
                "message": "DONE",
                "message_id": "tm-audit-once",
            }
        ).encode()
        signature = hmac.new(b"test-secret", payload, hashlib.sha512).hexdigest()

        api = APIClient()
        for _ in range(2):
            api.post(
                "/api/v1/messaging/webhooks/termii/",
                data=payload,
                content_type="application/json",
                HTTP_X_TERMII_SIGNATURE=signature,
            )
        acknowledgement_records = AuditLog.objects.filter(
            justification="Acknowledged by SMS reply."
        )
        assert acknowledgement_records.count() == 1


@pytest.mark.django_db
class TestReplyMatchingIgnoresUnsentMessages:
    def test_a_reply_never_matches_a_message_that_was_not_sent(
        self, facility, mentor_mother, open_alert
    ):
        from apps.messaging.gateway import handle_inbound
        from apps.messaging.models import InboundMessage, OutboundMessage

        OutboundMessage.objects.create(
            alert=open_alert,
            facility=facility,
            recipient_kind="MENTOR_MOTHER",
            recipient_msisdn="+2348010000000",
            recipient_reference=mentor_mother.staff_code,
            template_key="urgent_follow_up",
            status=OutboundMessage.Status.QUEUED,
            expects_reply=True,
        )
        inbound = handle_inbound("+2348010000000", "DONE")
        assert inbound.parse_status == InboundMessage.ParseStatus.UNMATCHED
        open_alert.refresh_from_db()
        assert open_alert.status == Alert.Status.OPEN


@pytest.mark.django_db
class TestWebhookThrottleIsReal:
    def test_the_callback_endpoint_is_rate_limited(
        self, settings, sent_message, monkeypatch
    ):
        import hashlib
        import hmac
        import json

        from django.core.cache import cache

        from apps.messaging.webhooks import TermiiWebhookThrottle

        cache.clear()
        settings.TERMII = {**settings.TERMII, "WEBHOOK_SECRET": "test-secret"}
        # THROTTLE_RATES binds to the class at import, so a settings override
        # cannot reach it; the class attribute is patched instead.
        monkeypatch.setattr(
            TermiiWebhookThrottle, "THROTTLE_RATES", {"inbound_sms": "2/hour"}
        )
        payload = json.dumps(
            {"type": "inbound", "sender": "+2348010000000", "message": "DONE",
             "message_id": "tm-throttle"}
        ).encode()
        signature = hmac.new(b"test-secret", payload, hashlib.sha512).hexdigest()

        api = APIClient()
        codes = [
            api.post(
                "/api/v1/messaging/webhooks/termii/",
                data=payload,
                content_type="application/json",
                HTTP_X_TERMII_SIGNATURE=signature,
            ).status_code
            for _ in range(3)
        ]
        cache.clear()
        assert codes[0] == 200 and codes[1] == 200
        # The third request must hit the ceiling: the endpoint is the only
        # unauthenticated write path in the system, and an unthrottled one
        # is a standing CPU-exhaustion invitation.
        assert codes[2] == 429


@pytest.mark.django_db
class TestIdentifierReadsAreAudited:
    def test_a_supervisor_reading_a_name_leaves_an_audit_record(
        self, api_client, client_row
    ):
        from apps.audit.models import AuditAction, AuditLog

        api_client.get(f"/api/v1/registry/clients/{client_row.pk}/")
        reveals = AuditLog.objects.filter(
            action=AuditAction.IDENTIFIER_REVEALED,
            object_id=str(client_row.pk),
        )
        assert reveals.count() == 1
        assert "full_name" in reveals.get().fields_touched

    def test_a_gated_role_reading_the_record_leaves_no_reveal_record(
        self, client_row, facility
    ):
        from apps.audit.models import AuditAction, AuditLog

        coordinator = User.objects.create(
            username="coordinator-audit",
            role=Role.LGA_COORDINATOR,
            lga=facility.lga,
            cognito_sub="test-sub-coord-audit",
        )
        _client_for(coordinator).get(f"/api/v1/registry/clients/{client_row.pk}/")
        assert not AuditLog.objects.filter(
            action=AuditAction.IDENTIFIER_REVEALED
        ).exists()


@pytest.mark.django_db
class TestFreeTextGating:
    def test_raw_sms_text_is_hidden_from_the_wider_tiers(
        self, api_client, facility, sent_message
    ):
        from apps.messaging.gateway import handle_inbound

        handle_inbound("+2348010000000", "DONE and the name Mary Okafor")

        supervisor_row = api_client.get("/api/v1/messaging/inbound/").json()[
            "results"
        ][0]
        assert "raw_body" in supervisor_row

        coordinator = User.objects.create(
            username="coordinator-freetext",
            role=Role.LGA_COORDINATOR,
            lga=facility.lga,
            cognito_sub="test-sub-coord-ft",
        )
        body = _client_for(coordinator).get("/api/v1/messaging/inbound/").content.decode()
        assert "Mary Okafor" not in body
        assert "raw_body" not in body


@pytest.mark.django_db
class TestInfantFacilityConsistency:
    def test_an_infant_cannot_be_registered_at_a_different_facility_from_the_mother(
        self, client_row, facility
    ):
        import datetime as _dt

        from apps.registry.models import Facility

        sibling = Facility.objects.create(
            lga=facility.lga, name="Second Centre", code="TS-01-002", level="PRIMARY"
        )
        coordinator = User.objects.create(
            username="coordinator-infant",
            role=Role.LGA_COORDINATOR,
            lga=facility.lga,
            cognito_sub="test-sub-coord-infant",
        )
        response = _client_for(coordinator).post(
            "/api/v1/registry/infants/",
            {
                "mother": client_row.client_code,
                "facility": str(sibling.pk),
                "date_of_birth": str(_dt.date.today()),
                "sex": "F",
            },
            format="json",
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestVisitReviewResolvesFlagAlert:
    def test_recording_a_review_closes_the_flag_alert_in_one_step(
        self, api_client, facility, mentor_mother, client_row, supervisor
    ):
        from apps.alerts.models import Alert, AlertRule
        from apps.visits.models import HomeVisit

        visit = HomeVisit.objects.create(
            client=client_row,
            mentor_mother=mentor_mother,
            purpose="ROUTINE",
            result="COMPLETED",
            visit_date=dt.date.today(),
            flagged_for_review=True,
            review_reason="Reported position is 900 m from the registered household.",
        )
        rule = AlertRule.objects.create(
            alert_type="VISIT_FLAG", severity="MEDIUM", threshold_value=1
        )
        alert = Alert.objects.create(
            rule=rule,
            alert_type="VISIT_FLAG",
            severity="MEDIUM",
            facility=facility,
            subject_type="visits.HomeVisit",
            subject_id=visit.pk,
            deduplication_key=f"VISIT_FLAG:{visit.pk}",
            title="A visit needs review",
        )
        response = api_client.post(
            f"/api/v1/visits/records/{visit.pk}/review/",
            {"review_outcome": "Household point was wrong; corrected at enrolment."},
            format="json",
        )
        assert response.status_code == 200
        alert.refresh_from_db()
        assert alert.status == Alert.Status.RESOLVED
        assert alert.resolution_note.startswith("Household point was wrong")
